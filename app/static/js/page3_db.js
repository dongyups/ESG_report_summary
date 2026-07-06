$(document).ready(function () {

    /* ──────────────────────────────────────
       공통: 로그아웃 / 인증 헤더
       timerInterval을 먼저 선언해 logout()의 TDZ 참조 오류 방지
    ────────────────────────────────────── */
    let timerInterval = null;

    function logout() {
        if (timerInterval) clearInterval(timerInterval);
        localStorage.clear();
        window.location.href = '/login';
    }

    // 핵심: 매 요청마다 localStorage에서 최신 토큰을 읽는다 (기존엔 로드 시 캡처한 token 재사용)
    // extra로 Content-Type 등 추가 헤더 병합 가능
    function authHeader(extra) {
        return Object.assign(
            { 'Authorization': `Bearer ${localStorage.getItem('access_token')}` },
            extra || {}
        );
    }

    /* ──────────────────────────────────────
       인증 / 타이머
    ────────────────────────────────────── */
    // 전역 Ajax 에러 핸들러 (401 → 로그아웃)
    $(document).ajaxError(function (event, jqXHR) {
        if (jqXHR.status === 401) {
            console.error('인증 에러: 토큰이 만료되었거나 유효하지 않습니다.');
            alert('세션이 만료되었습니다. 다시 로그인해주세요.');
            logout();
        }
    });

    // 사용자 정보 표시
    const username = localStorage.getItem('full_name') || localStorage.getItem('username') || '사용자';
    $('#userNameDisplay').text(username);

    // 토큰 로드 및 만료 시간 추출
    const token = localStorage.getItem('access_token');
    if (!token) return logout();

    function getTokenExpiration(t) {
        try {
            const base64 = t.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
            const jsonPayload = decodeURIComponent(
                atob(base64).split('').map(function (c) {
                    return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
                }).join('')
            );
            return JSON.parse(jsonPayload).exp * 1000;
        } catch (e) {
            console.error('토큰 파싱 실패:', e);
            return null;
        }
    }

    const tokenEndTime = getTokenExpiration(token);
    if (!tokenEndTime) {
        alert('유효하지 않은 접근입니다.');
        return logout();
    }

    // 실시간 타이머 업데이트
    timerInterval = setInterval(function () {
        const timeLeft = tokenEndTime - Date.now();

        if (timeLeft <= 0) {
            clearInterval(timerInterval);
            $('#tokenTimer').text('00:00:00');
            alert('세션이 만료되었습니다. 다시 로그인해주세요.');
            logout();
            return;
        }

        const h = Math.floor(timeLeft / (1000 * 60 * 60));
        const m = Math.floor((timeLeft % (1000 * 60 * 60)) / (1000 * 60));
        const s = Math.floor((timeLeft % (1000 * 60)) / 1000);

        $('#tokenTimer').text(
            String(h).padStart(2, '0') + ':' +
            String(m).padStart(2, '0') + ':' +
            String(s).padStart(2, '0')
        );
    }, 1000);

    // 로그아웃 버튼
    $('#logoutBtn').on('click', logout);

    /* ──────────────────────────────────────
       DB 관련 기능
    ────────────────────────────────────── */
    let currentTable = null;

    // 테이블 목록 로드
    function loadTables() {
        const tables = [
            { name: 'sk_hynix_e',        icon: '🌍', htmlName: 'E (환경) 데이터' },
            { name: 'sk_hynix_s',        icon: '👥', htmlName: 'S (사회) 데이터' },
            { name: 'sk_hynix_g',        icon: '🏛️', htmlName: 'G (경제/거버넌스) 데이터' },
            { name: 'sk_hynix_press',    icon: '📢', htmlName: 'ESG관련 보도자료 목록' },
            { name: 'sk_hynix_newsroom', icon: '📰', htmlName: 'ESG관련 뉴스기사 목록' },
            { name: 'sk_hynix_report',   icon: '📋', htmlName: 'SK하이닉스 2024년 ESG보고서' },
        ];

        const $list = $('#tablesList');
        $list.empty();

        tables.forEach(table => {
            const $item = $(`
                <div class="table-item" data-table="${table.name}">
                    <span class="table-icon">${table.icon}</span>
                    <span class="table-name">${table.htmlName}</span>
                </div>
            `);
            $list.append($item);
        });
    }

    // 테이블 클릭 이벤트
    $(document).on('click', '.table-item', function () {
        const tableName = $(this).data('table');
        loadTableData(tableName);

        $('.table-item').removeClass('active');
        $(this).addClass('active');
    });

    // 테이블 데이터 로드
    async function loadTableData(tableName) {
        currentTable = tableName;
        const $container = $('#tableContainer');

        // 로딩 표시
        $container.html('<div class="loading"><div class="loading-spinner"></div>데이터를 불러오는 중...</div>');
        $('#tableInfo').hide();

        try {
            const response = await fetch(`/db/tables/${tableName}`, {
                headers: authHeader()          // ← 캡처된 token 대신 매 요청 fresh-read
            });

            if (!response.ok) {
                throw new Error('데이터를 불러오는데 실패했습니다');
            }

            const data = await response.json();
            renderTable(tableName, data);
        } catch (error) {
            console.error('Error loading table:', error);
            $container.html(`
                <div class="error-message">
                    ❌ ${error.message}
                </div>
            `);
        }
    }

    // 테이블 렌더링
    function renderTable(tableName, data) {
        if (!data.columns || !data.rows || data.rows.length === 0) {
            $('#tableContainer').html(`
                <div class="empty-state">
                    <div class="empty-state-icon">📭</div>
                    <div class="empty-state-text">데이터가 없습니다</div>
                </div>
            `);
            return;
        }

        // 테이블 정보 업데이트
        $('#currentTableName').text(tableName);
        $('#rowCount').text(data.rows.length);
        $('#columnCount').text(data.columns.length);
        $('#tableInfo').show();

        // 테이블 HTML 생성
        let tableHTML = '<table class="data-table"><thead><tr>';

        // 헤더
        data.columns.forEach(col => {
            tableHTML += `<th>${escapeHtml(col)}</th>`;
        });
        tableHTML += '</tr></thead><tbody>';

        // 데이터 행
        data.rows.forEach(row => {
            tableHTML += '<tr>';
            data.columns.forEach(col => {
                const value = row[col];
                const displayValue = escapeHtml(String(value ?? ''));
                // URL 컬럼이면 클릭 가능한 스타일 적용
                if (col.toLowerCase() === 'url') {
                    tableHTML += `<td class="url-cell" data-url="${displayValue}">${displayValue}</td>`;
                } else {
                    tableHTML += `<td>${displayValue}</td>`;
                }
            });
            tableHTML += '</tr>';
        });
        tableHTML += '</tbody></table>';

        $('#tableContainer').html(tableHTML);

        // URL 셀 클릭 → 새 창에서 열기
        $('.url-cell').on('click', function () {
            const url = $(this).data('url');
            if (url) window.open(url, '_blank');
        });
    }

    // HTML 이스케이프
    function escapeHtml(text) {
        const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
        return text.replace(/[&<>"']/g, m => map[m]);
    }

    // 초기 로드
    loadTables();
});