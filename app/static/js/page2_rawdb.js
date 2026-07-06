/* ============================================================
   ESG Summary · Page2 RAW 데이터베이스
   page2_rawdb.js  (jQuery)

   [변경] 기존: 파일 내 인라인 RAW_DATA(수백 KB) 를 직접 렌더
          현재: page3 와 동일하게 백엔드 API 에서 DB 데이터를 fetch

   데이터 출처(서버 SQL) : GET /rawdb/raw/{RAW1~6}
   - RAW1  rawdata_ghg_quantity ⨝ rawdata_ghg_formula (전체 + 파생컬럼)
   - RAW2  └ item_name = '전력'
   - RAW3  └ item_name = '스팀 (유연탄 (연료용))'
   - RAW4  rawdata_erp  item_name IN (일반/지정 폐기물, 재활용, 매립)
   - RAW5  rawdata_erp  item_name IN (공업용수 취수량, 용수 재이용량)
   - RAW6  rawdata_erp  item_name = '폐수 방류량'

   * energy_usage / ghg_emissions 파생 컬럼은 서버 SQL 에서 산출된다.
   * 응답의 sql 문자열이 그대로 "실행 SQL 쿼리" 패널에 표시된다(단일 소스).
   * 사업장/기간/정렬은 서버가 반환한 뷰에 대해 클라이언트에서 처리한다
     (RAW 전환 시에만 fetch, 필터·정렬은 재요청 없음 · RAW별 캐시).
   ============================================================ */

/* ── RAW 메타(라벨은 화면 표기용) ──────────────────────────── */
const RAW_LABELS = {
    RAW1: 'RAW1 · 온실가스 (Scope 1 & 2)',
    RAW2: 'RAW2 · 전력 사용량 (Scope 2)',
    RAW3: 'RAW3 · 스팀 사용량 (Scope 2)',
    RAW4: 'RAW4 · 폐기물 배출',
    RAW5: 'RAW5 · 용수 / 수자원',
    RAW6: 'RAW6 · 폐수 및 수질',
};

/* 공통 컬럼: 두 스키마 모두 site_name / occur_ym 사용 */
const SITE_KEY = 'site_name';
const DATE_KEY = 'occur_ym';

/* 숫자 포맷 대상(측정/파생값)과 배지 대상 컬럼 */
const MEASURE_COLS = new Set(['collected_qty', 'energy_usage', 'ghg_emissions']);
const BADGE_COLS = new Set(['item_group']);

/* ── 상태 ──────────────────────────────────────────────────── */
let currentRaw = 'RAW1';
let currentSite = '전사';
let currentYear = '2024';
let rangeStart = 1;
let rangeEnd = 12;
let selectionStep = 0;      // 0=첫클릭대기, 1=종료월대기
let sortCol = null;
let sortDir = 'asc';

/* 서버에서 받은 현재 RAW 뷰 : {columns, rows, sql, source} */
let currentData = null;
/* 현재 화면에 표시된(사업장·기간 필터 + 정렬 적용) 행 → AI 그래프 생성 입력 */
let currentVisibleRows = [];
/* RAW별 응답 캐시 (전환 왕복 시 재요청 방지) */
const rawCache = Object.create(null);
let tokenTimer = null;

/* ── 인증 유틸(page3 패턴 정합) ────────────────────────────── */
function logout() {
    if (tokenTimer) clearInterval(tokenTimer);
    localStorage.clear();
    window.location.href = '/login';
}
// 매 요청마다 localStorage 에서 최신 토큰을 읽어 헤더 구성
function authHeader(extra) {
    return Object.assign(
        { 'Authorization': 'Bearer ' + (localStorage.getItem('access_token') || '') },
        extra || {}
    );
}
function getTokenExpiration(t) {
    try {
        const base64 = t.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
        const json = decodeURIComponent(atob(base64).split('').map(function (c) {
            return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
        }).join(''));
        return JSON.parse(json).exp * 1000;
    } catch (e) { console.error('토큰 파싱 실패:', e); return null; }
}

/* ── 데이터 로드 : 서버에서 RAW 뷰 fetch ──────────────────── */
async function loadRaw(rawType, force) {
    const $c = $('#tableContainer');

    // 캐시 히트(강제 새로고침 아님) → 즉시 렌더
    if (!force && rawCache[rawType]) {
        currentData = rawCache[rawType];
        renderSQL();
        renderData();
        return;
    }

    // 네트워크 로딩 표시
    currentData = null;
    $c.html('<div class="loading"><div class="loading-spinner"></div>데이터 로딩 중...</div>');
    $('#sqlSource').text('로딩 중…');

    try {
        const res = await fetch('/rawdb/raw/' + encodeURIComponent(rawType), { headers: authHeader() });

        if (res.status === 401) {                       // 토큰 만료/무효 → 로그아웃
            alert('세션이 만료되었습니다. 다시 로그인해주세요.');
            return logout();
        }
        if (!res.ok) throw new Error('데이터를 불러오지 못했습니다 (HTTP ' + res.status + ')');

        const data = await res.json();                  // {raw_type, source, sql, columns, rows}
        currentData = data;
        rawCache[rawType] = data;

        renderSQL();
        renderData();
    } catch (err) {
        console.error('RAW 로드 실패:', err);
        currentData = null;
        // page2 CSS 에는 .error-message 가 없으므로 기존 empty-state 스타일 재사용
        $c.html('<div class="empty-state"><div class="empty-state-icon">⚠️</div>'
            + '<div class="empty-state-text">데이터를 불러오지 못했습니다</div>'
            + '<div class="empty-state-subtext">' + esc(err.message) + '</div></div>');
        $('#sqlSource').text('-');
    }
}

/* ── 유틸 ──────────────────────────────────────────────────── */
function esc(t) {
    return String(t).replace(/[&<>"']/g, function (m) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[m];
    });
}

/* occur_ym 파싱: 'YYYY-MM'(GHG) 과 'YY-Mon'(ERP) 과 'Mon-YY' 세 형식 모두 지원 */
const MONTHS_EN = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 };
function parseYM(v) {
    if (v === null || v === undefined) return null;
    const s = String(v).trim();
    let m = s.match(/^(\d{4})-(\d{1,2})$/);            // 2024-01
    if (m) return { year: m[1], month: parseInt(m[2], 10) };
    m = s.match(/^(\d{2})-([A-Za-z]{3})$/);            // 24-Jan
    if (m) return { year: '20' + m[1], month: MONTHS_EN[m[2].toLowerCase()] || 0 };
    m = s.match(/^([A-Za-z]{3})-(\d{2})$/);            // Jan-24
    if(m) return {year:'20'+m[2], month:MONTHS_EN[m[1].toLowerCase()]||0};
    return null;
}

/* 셀 값 표시 포맷 */
function fmtCell(v, col) {
    if (v === null || v === undefined || v === '') return { text: '-', cls: '', empty: true };
    if (typeof v === 'number') {
        let t;
        if (MEASURE_COLS.has(col)) t = v.toLocaleString('en-US', { maximumFractionDigits: 2 });
        else if (Number.isInteger(v) && Math.abs(v) >= 1000) t = v.toLocaleString('en-US');
        else t = String(v);
        return { text: t, cls: 'num', empty: false };
    }
    return { text: esc(v), cls: '', empty: false, raw: v };
}

/* Scope / 폐기물·용수 그룹 배지 */
function badge(v) {
    if (v === '-' || v === null || v === undefined || v === '')
        return '<span style="color:#ccc">-</span>';
    const s = String(v);
    let c = 'b-etc';
    if (s === 'Scope 1') c = 'b-s1';
    else if (s === 'Scope 2') c = 'b-s2';
    else if (s === 'Scope 1&2') c = 'b-s12';
    else if (s.indexOf('Scope 3') === 0) c = 'b-s3';
    else if (s === 'Recycle') c = 'b-rec';
    else if (s === 'Disposal') c = 'b-dis';
    else if (s === 'Output') c = 'b-out';
    else if (s === 'Input') c = 'b-in';
    return '<span class="badge ' + c + '">' + esc(s) + '</span>';
}

/* 간이 SQL 하이라이트 */
function hlSQL(sql) {
    const KW = ['WITH', 'SELECT', 'FROM', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'ON', 'WHERE',
        'AND', 'OR', 'IN', 'AS', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'ORDER', 'GROUP',
        'HAVING', 'BY', 'ASC', 'DESC', 'NOT', 'NULL', 'LIMIT', 'DISTINCT'];
    const FN = ['POW', 'SUM', 'ROUND', 'COUNT', 'AVG', 'MAX', 'MIN', 'COALESCE', 'CAST'];
    const TB = ['rawdata_ghg_quantity', 'rawdata_ghg_formula', 'rawdata_erp', 'rawdata_ghg'];
    return sql.split('\n').map(function (line) {
        let escd = esc(line);
        let ci = escd.indexOf('--');
        let code = ci >= 0 ? escd.slice(0, ci) : escd;
        let comment = ci >= 0 ? escd.slice(ci) : '';
        // 문자열 보호 (비-단어·비-숫자 사설영역 문자로 치환 → 이후 정규식이 인덱스를 훼손하지 못함)
        let strs = [];
        code = code.replace(/&#039;[^&]*?&#039;/g, function (m) { strs.push(m); return '\uE000' + String.fromCharCode(0xE100 + strs.length - 1) + '\uE001'; });
        // 키워드 / 함수 / 테이블 / 숫자
        code = code.replace(new RegExp('\\b(' + KW.join('|') + ')\\b', 'g'), '<span class="kw">$1</span>');
        code = code.replace(new RegExp('\\b(' + FN.join('|') + ')\\b', 'g'), '<span class="fn">$1</span>');
        code = code.replace(new RegExp('\\b(' + TB.join('|') + ')\\b', 'g'), '<span class="tb">$1</span>');
        code = code.replace(/\b(\d+(?:\.\d+)?(?:e-?\d+)?)\b/gi, '<span class="num">$1</span>');
        // 문자열 복원
        code = code.replace(/\uE000([\s\S])\uE001/g, function (_, ch) { return '<span class="str">' + strs[ch.charCodeAt(0) - 0xE100] + '</span>'; });
        if (comment) comment = '<span class="cm">' + comment + '</span>';
        return code + comment;
    }).join('\n');
}

/* ── 월 범위 UI ────────────────────────────────────────────── */
function updateMonthRangeUI() {
    $('.month-cell').each(function () {
        const m = parseInt($(this).data('month'), 10);
        $(this).removeClass('range-start range-end range-in selecting');
        if (selectionStep === 1 && m === rangeStart) $(this).addClass('selecting');
        else if (m === rangeStart && m === rangeEnd) $(this).addClass('range-start range-end');
        else if (m === rangeStart) $(this).addClass('range-start');
        else if (m === rangeEnd) $(this).addClass('range-end');
        else if (m > rangeStart && m < rangeEnd) $(this).addClass('range-in');
    });
}

/* ── SQL 패널(서버가 반환한 sql 을 그대로 표시) ───────────── */
function renderSQL() {
    const sql = (currentData && currentData.sql) || '';
    $('#sqlCode').html('<code>' + hlSQL(sql) + '</code>');
    $('#sqlSource').text((currentData && currentData.source) || '');
    $('#sqlCopy').removeClass('copied').text('복사').data('sql', sql);
}

/* ── 렌더링 소스(서버 응답 기반) ──────────────────────────── */
function getSource() {
    if (!currentData) return { headers: [], rows: [], label: RAW_LABELS[currentRaw] || currentRaw };
    return { headers: currentData.columns, rows: currentData.rows, label: RAW_LABELS[currentRaw] || currentRaw };
}

function renderData() {
    if (!currentData) return;                         // 데이터 로드 전/실패 시 렌더 스킵
    const $c = $('#tableContainer');
    $c.html('<div class="loading"><div class="loading-spinner"></div>데이터를 불러오는 중...</div>');

    setTimeout(function () {
        const src = getSource();
        let rows = src.rows.slice();

        // 사업장 필터
        if (currentSite !== '전사') rows = rows.filter(function (r) { return r[SITE_KEY] === currentSite; });

        // 연도·월 범위 필터
        rows = rows.filter(function (r) {
            const ym = parseYM(r[DATE_KEY]);
            if (!ym) return false;
            return ym.year === currentYear && ym.month >= rangeStart && ym.month <= rangeEnd;
        });

        // 정렬
        if (sortCol) {
            rows.sort(function (a, b) {
                let va = a[sortCol], vb = b[sortCol];
                if (va === '' || va == null) return 1;
                if (vb === '' || vb == null) return -1;
                if (typeof va === 'number' && typeof vb === 'number')
                    return sortDir === 'asc' ? va - vb : vb - va;
                if (!isNaN(+va) && !isNaN(+vb))
                    return sortDir === 'asc' ? (+va) - (+vb) : (+vb) - (+va);
                return sortDir === 'asc'
                    ? String(va).localeCompare(String(vb), 'ko')
                    : String(vb).localeCompare(String(va), 'ko');
            });
        }

        // AI 그래프 생성 입력용: 현재 화면 표시 행(필터·정렬 반영) 캡처
        currentVisibleRows = rows;

        // 정보 업데이트
        $('#currentRawLabel').text(src.label);
        $('#rowCount').text(rows.length.toLocaleString());
        $('#columnCount').text(src.headers.length);
        $('#currentSiteLabel').text(currentSite);
        const rangeLabel = rangeStart === rangeEnd
            ? currentYear + '-' + String(rangeStart).padStart(2, '0')
            : currentYear + ' ' + rangeStart + '월~' + rangeEnd + '월';
        $('#currentMonthLabel').text(rangeLabel);
        $('#contentSubtitle').text(src.label + ' 데이터');

        // AI 그래프 생성 패널 컨텍스트 동기화 (구 스냅샷 대체)
        $('#cgRawLabel').text(currentRaw);
        $('#cgSite').text(currentSite);
        $('#cgPeriod').text(rangeStart === rangeEnd
            ? currentYear + '년 ' + rangeStart + '월'
            : currentYear + '년 ' + rangeStart + '~' + rangeEnd + '월');
        $('#cgRows').text(rows.length.toLocaleString());

        // 빈 상태
        if (rows.length === 0) {
            $c.html('<div class="empty-state"><div class="empty-state-icon">📭</div>'
                + '<div class="empty-state-text">해당 조건의 데이터가 없습니다</div>'
                + '<div class="empty-state-subtext">' + esc(currentSite) + ' / ' + esc(rangeLabel) + ' 조건의 데이터가 없습니다</div></div>');
            return;
        }

        // 테이블
        let h = '<table class="data-table"><thead><tr>';
        src.headers.forEach(function (col) {
            const active = sortCol === col;
            const icon = active ? (sortDir === 'asc' ? '▲' : '▼') : '⇅';
            const cls = active ? 'sort-' + sortDir : '';
            h += '<th data-col="' + esc(col) + '" class="' + cls + '">' + esc(col) + '<span class="sort-icon">' + icon + '</span></th>';
        });
        h += '</tr></thead><tbody>';

        rows.forEach(function (row) {
            h += '<tr>';
            src.headers.forEach(function (col) {
                const f = fmtCell(row[col], col);
                if (BADGE_COLS.has(col)) h += '<td>' + badge(f.empty ? '-' : (f.raw !== undefined ? f.raw : row[col])) + '</td>';
                else h += '<td class="' + f.cls + '">' + f.text + '</td>';
            });
            h += '</tr>';
        });
        h += '</tbody></table>';
        $c.html(h);

        // 헤더 클릭 정렬
        $c.find('th[data-col]').on('click', function () {
            const c = $(this).data('col');
            if (sortCol === c) sortDir = (sortDir === 'asc' ? 'desc' : 'asc');
            else { sortCol = c; sortDir = 'asc'; }
            renderData();
        });
    }, 60);
}

/* ── 초기화 / 이벤트 바인딩 ────────────────────────────────── */
$(function () {

    // 인증: 토큰 없으면 로그인으로
    const token = localStorage.getItem('access_token');
    if (!token) return logout();

    // jQuery ajax 전역 401 (본 페이지는 fetch 라 loadRaw 에서도 별도 처리)
    $(document).ajaxError(function (_e, jqXHR) {
        if (jqXHR.status === 401) { alert('세션이 만료되었습니다. 다시 로그인해주세요.'); logout(); }
    });

    // 사용자 정보
    const uname = localStorage.getItem('full_name') || localStorage.getItem('username') || '사용자';
    $('#userNameDisplay').text(uname);

    // 토큰 만료 타이머
    const end = getTokenExpiration(token);
    if (!end) { alert('유효하지 않은 접근입니다.'); return logout(); }
    tokenTimer = setInterval(function () {
        const left = end - Date.now();
        if (left <= 0) {
            clearInterval(tokenTimer);
            $('#tokenTimer').text('00:00:00');
            alert('세션이 만료되었습니다. 다시 로그인해주세요.');
            return logout();
        }
        const hh = Math.floor(left / 3600000),
            mm = Math.floor((left % 3600000) / 60000),
            ss = Math.floor((left % 60000) / 1000);
        $('#tokenTimer').text(String(hh).padStart(2, '0') + ':' + String(mm).padStart(2, '0') + ':' + String(ss).padStart(2, '0'));
    }, 1000);

    $('#logoutBtn').on('click', logout);

    // 기본 선택 RAW 표시
    $('.raw-menu-item[data-raw="' + currentRaw + '"]').addClass('active');

    // RAW 메뉴 → 서버에서 해당 RAW 뷰 fetch (renderSQL/renderData 는 loadRaw 내부에서 호출)
    $('.raw-menu-item').on('click', function () {
        currentRaw = $(this).data('raw');
        sortCol = null; sortDir = 'asc';
        $('.raw-menu-item').removeClass('active');
        $(this).addClass('active');
        loadRaw(currentRaw);
    });

    // 사업장/연도/월 필터 · 정렬 : 이미 받은 뷰에 대해 클라이언트 처리(재요청 없음)
    $('#siteSelect').on('change', function () { currentSite = $(this).val(); renderData(); });
    $('#yearSelect').on('change', function () { currentYear = $(this).val(); renderData(); });
    $('.month-cell').on('click', function () {
        const m = parseInt($(this).data('month'), 10);
        if (selectionStep === 0 || m < rangeStart) { rangeStart = m; rangeEnd = m; selectionStep = 1; }
        else { rangeEnd = m; selectionStep = 0; }
        updateMonthRangeUI();
        renderData();
    });

    // SQL 패널 토글
    $('#sqlToggle').on('click', function (e) {
        if ($(e.target).closest('#sqlCopy').length) return;   // 복사 버튼 제외
        $('#sqlPanel').toggleClass('open');
    });

    // SQL 복사
    $('#sqlCopy').on('click', function () {
        const sql = $(this).data('sql') || (currentData && currentData.sql) || '';
        const $btn = $(this);
        const done = function () {
            $btn.addClass('copied').text('복사됨 ✓');
            setTimeout(function () { $btn.removeClass('copied').text('복사'); }, 1500);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(sql).then(done, done);
        } else {
            const ta = document.createElement('textarea');
            ta.value = sql; document.body.appendChild(ta); ta.select();
            try { document.execCommand('copy'); } catch (err) { }
            document.body.removeChild(ta); done();
        }
    });

    // 최초: 월범위 UI + 기본 RAW 로드
    updateMonthRangeUI();
    loadRaw(currentRaw);
});

/* ============================================================
   AI 그래프 생성 기능 (구 대시보드 스냅샷 대체)
   ------------------------------------------------------------
   흐름:
   1) 현재 화면에 표시된 행(currentVisibleRows) + 컬럼/컨텍스트를
      POST /rawdb/chart 로 전송한다.
   2) 서버가 config.py 의 RAG_LLM_MODEL 로 "어떤 그래프를 그릴지"
      스펙(JSON: chart_type/x_field/y_field/agg/group_by/라벨/인사이트)만
      결정해 반환한다. (수치 계산은 LLM 이 하지 않음)
   3) 클라이언트가 실제 데이터로 집계 후 Chart.js 로 렌더한다.
   4) 모달의 "이미지 저장" 으로 PNG 다운로드 → ESG 보고서에 활용.
   ============================================================ */

/* 테마 색상 팔레트(헤더 그라디언트 계열) */
const CHART_PALETTE = ['#1a448c', '#9072ad', '#2e7d32', '#e65100', '#00796b',
                       '#6a1b9a', '#1565c0', '#bf360c', '#455a64', '#ad1457'];

let esgChart = null;   // Chart.js 인스턴스(재생성 시 파괴)

/* 저장 이미지 투명 배경 방지: 캔버스 뒤를 흰색으로 채움 */
const whiteBgPlugin = {
    id: 'whiteBg',
    beforeDraw: function (chart) {
        const ctx = chart.ctx;
        ctx.save();
        ctx.globalCompositeOperation = 'destination-over';
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, chart.width, chart.height);
        ctx.restore();
    }
};

/* 축·툴팁용 큰 숫자 축약 */
function cgFmtNum(v) {
    if (v == null || isNaN(v)) return v;
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(1) + 'B';
    if (a >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (a >= 1e3) return (v / 1e3).toFixed(1) + 'K';
    return (Math.round(v * 100) / 100).toLocaleString('en-US');
}

/* spec(x_field/y_field/agg/group_by) 기준으로 실제 데이터 집계 → Chart.js 데이터 */
function aggregateForChart(rows, spec) {
    const xf = spec.x_field, yf = spec.y_field, gb = spec.group_by;
    const agg = (spec.agg || 'sum').toLowerCase();

    function applyAgg(list) {
        if (agg === 'count') return list.length;
        const nums = list.map(function (r) { return Number(r[yf]); })
                         .filter(function (n) { return !isNaN(n); });
        if (!nums.length) return 0;
        const sum = nums.reduce(function (a, b) { return a + b; }, 0);
        return agg === 'avg' ? sum / nums.length : sum;
    }

    // X축 라벨 수집
    let labels = Array.from(new Set(rows.map(function (r) { return r[xf]; })))
                      .filter(function (v) { return v != null && v !== ''; });

    // 정렬: 기간(occur_ym)은 연·월 파싱 순, 숫자는 수치 순, 그 외 로케일 순
    if (xf === DATE_KEY) {
        labels.sort(function (a, b) {
            const pa = parseYM(a), pb = parseYM(b);
            if (pa && pb) return pa.year !== pb.year ? (pa.year < pb.year ? -1 : 1) : pa.month - pb.month;
            return String(a).localeCompare(String(b), 'ko');
        });
    } else {
        labels.sort(function (a, b) {
            const na = Number(a), nb = Number(b);
            if (!isNaN(na) && !isNaN(nb)) return na - nb;
            return String(a).localeCompare(String(b), 'ko');
        });
    }

    let datasets;
    if (gb) {
        const groups = Array.from(new Set(rows.map(function (r) { return r[gb]; })))
                            .filter(function (v) { return v != null && v !== ''; })
                            .sort(function (a, b) { return String(a).localeCompare(String(b), 'ko'); });
        datasets = groups.map(function (g, i) {
            const data = labels.map(function (x) {
                return applyAgg(rows.filter(function (r) { return r[xf] === x && r[gb] === g; }));
            });
            return { label: String(g), data: data, _color: CHART_PALETTE[i % CHART_PALETTE.length] };
        });
    } else {
        const data = labels.map(function (x) {
            return applyAgg(rows.filter(function (r) { return r[xf] === x; }));
        });
        datasets = [{ label: spec.y_label || yf || '값', data: data, _color: CHART_PALETTE[0] }];
    }
    return { labels: labels.map(String), datasets: datasets };
}

/* Chart.js 렌더 */
function renderEsgChart(spec, agg) {
    if (esgChart) { esgChart.destroy(); esgChart = null; }

    const t = (spec.chart_type || 'bar').toLowerCase();
    let baseType = 'bar', stacked = false;
    if (t === 'line') baseType = 'line';
    else if (t === 'pie') baseType = 'pie';
    else if (t === 'doughnut') baseType = 'doughnut';
    else if (t === 'stacked-bar') { baseType = 'bar'; stacked = true; }
    else baseType = 'bar'; // bar / grouped-bar

    const isCircular = (baseType === 'pie' || baseType === 'doughnut');

    const datasets = agg.datasets.map(function (ds) {
        if (isCircular) {
            return {
                label: ds.label, data: ds.data,
                backgroundColor: agg.labels.map(function (_, i) { return CHART_PALETTE[i % CHART_PALETTE.length]; }),
                borderColor: '#fff', borderWidth: 2
            };
        }
        if (baseType === 'line') {
            return {
                label: ds.label, data: ds.data,
                borderColor: ds._color, backgroundColor: ds._color + '22',
                borderWidth: 2, tension: 0.3, pointRadius: 3, pointHoverRadius: 5, fill: false
            };
        }
        return {
            label: ds.label, data: ds.data,
            backgroundColor: ds._color, borderColor: ds._color, borderWidth: 1,
            borderRadius: 4, maxBarThickness: 46
        };
    });

    const showLegend = isCircular || datasets.length > 1;

    const scales = isCircular ? {} : {
        x: {
            stacked: stacked,
            title: { display: !!spec.x_label, text: spec.x_label || '', font: { size: 12, weight: '600' }, color: '#555' },
            ticks: { color: '#666', font: { size: 11 }, maxRotation: 45, minRotation: 0 },
            grid: { display: false }
        },
        y: {
            stacked: stacked, beginAtZero: true,
            title: { display: !!spec.y_label, text: spec.y_label || '', font: { size: 12, weight: '600' }, color: '#555' },
            ticks: { color: '#666', font: { size: 11 }, callback: function (v) { return cgFmtNum(v); } },
            grid: { color: '#eef0f5' }
        }
    };

    const ctx = document.getElementById('esgChartCanvas').getContext('2d');
    esgChart = new Chart(ctx, {
        type: baseType,
        data: { labels: agg.labels, datasets: datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            devicePixelRatio: 2,          // 저장 이미지 고해상도
            animation: { duration: 350 },
            layout: { padding: 10 },
            plugins: {
                title: {
                    display: !!spec.title, text: spec.title || '',
                    font: { size: 16, weight: '700' }, color: '#1a448c',
                    padding: { top: 4, bottom: 14 }
                },
                legend: {
                    display: showLegend, position: isCircular ? 'right' : 'top',
                    labels: { color: '#444', font: { size: 11 }, usePointStyle: true, boxWidth: 8 }
                },
                tooltip: {
                    callbacks: {
                        label: function (c) {
                            const val = isCircular ? c.parsed : c.parsed.y;
                            const pre = c.dataset.label ? c.dataset.label + ': ' : '';
                            return ' ' + pre + Number(val).toLocaleString('en-US', { maximumFractionDigits: 2 });
                        }
                    }
                }
            },
            scales: scales
        },
        plugins: [whiteBgPlugin]
    });
}

/* 저장 파일명 */
function buildChartFilename(spec) {
    const d = new Date();
    const pad = function (x) { return String(x).padStart(2, '0'); };
    const ts = '' + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + '_' + pad(d.getHours()) + pad(d.getMinutes());
    return 'ESG_' + currentRaw + '_' + (spec.chart_type || 'chart') + '_' + ts + '.png';
}

/* 모달 열기 + 서버(LLM) 스펙 요청 + 렌더 */
function openChartModal() {
    const rows = (currentVisibleRows || []).slice();
    if (!rows.length) {
        alert('표시된 데이터가 없습니다. 필터를 조정한 뒤 다시 시도하세요.');
        return;
    }

    // 로딩 상태로 모달 오픈
    $('#chartModalOverlay').addClass('show');
    $('#chartModalError').hide().text('');
    $('#chartCanvasWrap').hide();
    $('#chartInsight').hide().empty();
    $('#chartModalMeta').text('');
    $('#chartSaveBtn').prop('disabled', true).removeData('fname');
    $('#chartModalLoading').show();
    $('#genChartBtn').prop('disabled', true);
    $('#chartModalTitle').text('AI 그래프 생성 · ' + currentRaw);

    const periodLabel = (rangeStart === rangeEnd)
        ? currentYear + '년 ' + rangeStart + '월'
        : currentYear + '년 ' + rangeStart + '~' + rangeEnd + '월';

    const columns = (currentData && currentData.columns) || Object.keys(rows[0] || {});

    // LLM 은 '구조'만 결정 → 샘플 40행 + 컬럼/컨텍스트면 충분(토큰 절약)
    const payload = {
        raw_type: currentRaw,
        columns: columns,
        rows: rows.slice(0, 40),
        row_count: rows.length,
        site: currentSite,
        period: periodLabel
    };

    fetch('/rawdb/chart', {
        method: 'POST',
        headers: authHeader({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload)
    }).then(function (res) {
        if (res.status === 401) { alert('세션이 만료되었습니다. 다시 로그인해주세요.'); logout(); throw new Error('__401__'); }
        if (!res.ok) {
            return res.json().then(
                function (e) { throw new Error(e.detail || ('HTTP ' + res.status)); },
                function () { throw new Error('HTTP ' + res.status); }
            );
        }
        return res.json();
    }).then(function (spec) {
        // LLM 이 유효하지 않은 컬럼을 지목한 경우 방어(서버에서도 검증하지만 이중 방어)
        if (columns.indexOf(spec.x_field) < 0) throw new Error('AI가 반환한 X축 컬럼이 유효하지 않습니다: ' + spec.x_field);
        if (spec.group_by && columns.indexOf(spec.group_by) < 0) spec.group_by = null;
        if ((spec.agg || 'sum') !== 'count' && spec.y_field && columns.indexOf(spec.y_field) < 0) {
            throw new Error('AI가 반환한 Y축 컬럼이 유효하지 않습니다: ' + spec.y_field);
        }

        const aggregated = aggregateForChart(rows, spec);

        $('#chartModalLoading').hide();
        $('#chartCanvasWrap').show();
        renderEsgChart(spec, aggregated);

        if (spec.insight) $('#chartInsight').show().html('<b>💡 인사이트</b> ' + esc(spec.insight));
        $('#chartModalMeta').text(RAW_LABELS[currentRaw] + ' · ' + currentSite + ' · ' + periodLabel + ' · ' + rows.length + '행');
        $('#chartSaveBtn').prop('disabled', false).data('fname', buildChartFilename(spec));
    }).catch(function (err) {
        if (err && err.message === '__401__') return;
        $('#chartModalLoading').hide();
        $('#chartModalError').show().text('그래프 생성 실패: ' + (err && err.message ? err.message : err));
        console.error('그래프 생성 실패:', err);
    }).then(function () {
        $('#genChartBtn').prop('disabled', false);
    });
}

/* 현재 그래프를 PNG 로 저장 */
function saveChartImage() {
    if (!esgChart) return;
    const url = esgChart.toBase64Image('image/png', 1.0);
    const a = document.createElement('a');
    a.href = url;
    a.download = $('#chartSaveBtn').data('fname') || ('ESG_' + currentRaw + '_chart.png');
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

/* 모달 닫기 */
function closeChartModal() {
    $('#chartModalOverlay').removeClass('show');
    if (esgChart) { esgChart.destroy(); esgChart = null; }
}

/* AI 그래프 기능 이벤트 바인딩(기존 초기화와 분리된 별도 ready 블록) */
$(function () {
    $('#genChartBtn').on('click', openChartModal);
    $('#chartSaveBtn').on('click', saveChartImage);
    $('#chartCloseBtn').on('click', closeChartModal);
    $('#chartModalX').on('click', closeChartModal);

    // 오버레이 바깥 클릭 시 닫기
    $('#chartModalOverlay').on('click', function (e) {
        if (e.target === this) closeChartModal();
    });
    // ESC 로 닫기
    $(document).on('keydown', function (e) {
        if (e.key === 'Escape' && $('#chartModalOverlay').hasClass('show')) closeChartModal();
    });
});
