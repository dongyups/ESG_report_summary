$(document).ready(function () {

    /* ──────────────────────────────────────
       1. 인증 / 타이머 (page4와 동일)
    ────────────────────────────────────── */
    let timerInterval = null; // TDZ 방지
    $(document).ajaxError(function (e, xhr) {
        if (xhr.status === 401) { alert('세션이 만료되었습니다.'); logout(); }
    });

    const username = localStorage.getItem('full_name') || localStorage.getItem('username') || '사용자';
    $('#userNameDisplay').text(username);

    const token = localStorage.getItem('access_token');
    if (!token) return logout();

    function getExp(t) {
        try {
            const b = t.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
            return JSON.parse(decodeURIComponent(
                atob(b).split('').map(c => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2)).join('')
            )).exp * 1000;
        } catch { return null; }
    }

    const expTime = getExp(token);
    if (!expTime) { alert('유효하지 않은 접근입니다.'); return logout(); }

    timerInterval = setInterval(function () {
        const left = expTime - Date.now();
        if (left <= 0) { clearInterval(timerInterval); alert('세션 만료'); logout(); return; }
        const h = String(Math.floor(left / 3600000)).padStart(2, '0');
        const m = String(Math.floor(left % 3600000 / 60000)).padStart(2, '0');
        const s = String(Math.floor(left % 60000 / 1000)).padStart(2, '0');
        $('#tokenTimer').text(`${h}:${m}:${s}`);
    }, 1000);

    $('#logoutBtn').on('click', logout);
    function logout() {
        clearInterval(timerInterval);
        localStorage.clear();
        window.location.href = '/login';
    }

    /* ──────────────────────────────────────
       2. 공통 유틸
    ────────────────────────────────────── */
    function authHeader() {
        return { 'Authorization': `Bearer ${localStorage.getItem('access_token')}` };
    }

    function escHtml(s) {
        return String(s).replace(/[&<>"']/g, m => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
        }[m]));
    }

    // 텍스트 복사 http미지원 에러: navigator.clipboard.writeText 대체
    function copyText(text) {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.style.position = "fixed";
        textarea.style.left = "-9999px";

        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();

        const success = document.execCommand("copy");
        document.body.removeChild(textarea);

        return success;
    }

    /* ──────────────────────────────────────
       3. ChromaDB 상태 조회 / 인덱싱 (page4와 동일)
    ────────────────────────────────────── */
    async function loadStatus() {
        try {
            const res = await fetch('/rag/status', { headers: authHeader() });
            if (!res.ok) return;
            const data = await res.json();
            $('#pressCount').text(data.sk_hynix_press    ?? 0);
            $('#newsroomCount').text(data.sk_hynix_newsroom ?? 0);
            $('#reportCount').text(data.sk_hynix_report   ?? 0);
            $('#esgdataCount').text(data.sk_hynix_esg_data ?? 0);
        } catch (e) { console.error(e); }
    }

    $('#indexBtn').on('click', async function () {
        const $btn  = $(this);
        const $prog = $('#indexProgress');
        $btn.prop('disabled', true).text('인덱싱 중...');
        $prog.show().html('');
        try {
            const res     = await fetch('/rag/index', { method: 'POST', headers: authHeader() });
            const reader  = res.body.getReader();
            const decoder = new TextDecoder();
            let buf = '';
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buf += decoder.decode(value, { stream: true });

                const lines = buf.split('\n');
                buf = lines.pop();                 // 미완성 라인 보존

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    let d;
                    try { d = JSON.parse(line.slice(6)); } catch { continue; }
                    $prog.append(`<div>${escHtml(d.message)}</div>`);
                    if (d.done) { await loadStatus(); }
                }
            }
        } catch (e) {
            $prog.append(`<div style="color:red">오류: ${escHtml(e.message)}</div>`);
        } finally {
            $btn.prop('disabled', false).text('ChromaDB 인덱싱 ⟳');
        }
    });

    /* ──────────────────────────────────────
       4. 앱 상태
    ────────────────────────────────────── */
    let isGenerating    = false;
    let totalSections   = 0;
    let completedCount  = 0;
    let finalReportText = '';

    /* ──────────────────────────────────────
       4-1. 앱 생성된 결과물 화면저장 용
    ────────────────────────────────────── */
    const REPORT_STATE_KEY = 'esg_report_state';

    function saveReportState() {
        const sections = [];
        $('#sectionsGrid .section-card').each(function () {
            const $c = $(this);
            sections.push({
                title: $c.data('title'),
                idx: $c.data('idx'),
                category: $c.find('.cat-badge').text(),
                status: $c.hasClass('done') ? 'done' : $c.hasClass('processing') ? 'processing' : 'pending',
                draft: $c.find('.section-draft-text').text(),
                sourcesHtml: $c.find('.section-sources-holder').html() || ''
            });
        });
        localStorage.setItem(REPORT_STATE_KEY, JSON.stringify({
            targetYear: $('#targetYear').val(),
            scope: $('#scopeSelect').val(),
            sections, totalSections, completedCount, finalReportText,
            finalReportLabel: $('#finalReportLabel').text(),
            synthesisDone: $('#synthesisBadge').hasClass('done'),
            showSectionsPanel: $('#sectionsPanel').is(':visible'),
        }));
    }

    function clearReportState() {
        localStorage.removeItem(REPORT_STATE_KEY);
    }

    function restoreReportState() {
        const raw = localStorage.getItem(REPORT_STATE_KEY);
        if (!raw) return;
        let state;
        try { state = JSON.parse(raw); } catch { return; }
        if (!state.sections || !state.sections.length) return;

        totalSections   = state.totalSections;
        completedCount  = state.completedCount;
        finalReportText = state.finalReportText || '';
        $('#targetYear').val(state.targetYear);
        $('#scopeSelect').val(state.scope);
        $('#emptyState').hide();

        const $grid = $('#sectionsGrid').empty();
        state.sections.forEach(s => {
            const $card = buildSectionCard(s.title, s.category, s.idx);
            if (s.status === 'done') {
                $card.removeClass('pending').addClass('done');
                $card.find('.section-status-icon').text('✅');
                $card.find('.section-draft-text').text(s.draft || '');
                $card.find('.section-done-content').show();
                $card.find('.section-sources-holder').html(s.sourcesHtml || '');
                const draft = s.draft || '';
                $card.find('.copy-section-btn').on('click', function () {
                    const $btn = $(this);
                    // navigator.clipboard.writeText(draft).then(() => {
                    //     $btn.text('✅ 복사됨'); setTimeout(() => $btn.text('📋 복사'), 1500);
                    // });
                    copyText(draft) ? $btn.text('✅ 복사됨') : $btn.text('❌ 복사 실패');
                    setTimeout(() => $btn.text('📋 복사'), 1500);
                });
            } else if (s.status === 'processing') {
                $card.removeClass('pending').addClass('processing');
                $card.find('.section-status-icon').text('⚙️');
                $card.find('.section-processing-msg').show();
            }
            $grid.append($card);
        });
        if (state.showSectionsPanel) $('#sectionsPanel').show();
        updateSectionCounter();

        if (state.finalReportText) {
            $('#finalReportLabel').text(state.finalReportLabel || '최종 ESG 보고서');
            $('#finalReportContent').text(state.finalReportText);
            $('#finalReportPanel').show();
            if (state.synthesisDone) {
                $('#synthesisBadge').text('✅ 합성 완료').addClass('done');
                $('#copyReportBtn').prop('disabled', false).on('click', function () {
                    const $btn = $(this);
                    // navigator.clipboard.writeText(finalReportText).then(() => {
                    //     $btn.text('✅ 복사됨'); setTimeout(() => $btn.text('📋 전체 복사'), 1600);
                    // });
                    copyText(finalReportText) ? $btn.text('✅ 복사됨') : $btn.text('❌ 복사 실패');
                    setTimeout(() => $btn.text('📋 전체 복사'), 1600);
                });
            }
        }
        $('#progressBar').show();
        updateProgress(state.synthesisDone ? 4 : (state.finalReportText ? 3 : 2));
    }

    /* ──────────────────────────────────────
       5. 진행 단계 바 (4단계)
         1 = 계획  2 = 섹션생성  3 = 합성  4 = 완료
    ────────────────────────────────────── */
    function updateProgress(step) {
        for (let i = 1; i <= 4; i++) {
            const $s = $(`#pStep${i}`);
            $s.removeClass('active completed');
            if (i < step)       $s.addClass('completed');
            else if (i === step) $s.addClass('active');
        }
    }

    /* 섹션 카운터 레이블 갱신 */
    function updateSectionCounter() {
        $('#sectionsCounter').text(`${completedCount} / ${totalSections} 완료`);
        $('#pStep2Label').text(
            completedCount >= totalSections
                ? `섹션 생성 (${totalSections}/${totalSections})`
                : `섹션 생성 중 (${completedCount}/${totalSections})`
        );
    }

    /* ──────────────────────────────────────
       6. 보고서 생성 시작
         POST /rag/report → SSE 스트림
    ────────────────────────────────────── */
    $('#generateBtn').on('click', async function () {
        if (isGenerating) return;

        const targetYear = $('#targetYear').val();
        const scope      = $('#scopeSelect').val();

        // 상태 초기화
        isGenerating   = true;
        totalSections  = 0;
        completedCount = 0;
        finalReportText = '';
        clearReportState(); // 기존 저장값 제거

        // UI 초기화
        $('#emptyState').hide();
        $('#errorPanel').hide();
        $('#sectionsPanel').hide();
        $('#sectionsGrid').empty();
        $('#finalReportPanel').hide();
        $('#finalReportContent').empty();
        $('#progressBar').show();
        updateProgress(1);

        const $btn = $(this);
        $btn.prop('disabled', true).find('span:last').text('생성 중...');

        // 제목 갱신
        const scopeLabel = { '전체': '전체', 'I': '도입·개요(I)', 'E': '환경(E)', 'S': '사회(S)', 'G': '지배구조(G)' }[scope] || scope;
        $('#finalReportLabel').text(`최종 ESG 보고서 — ${targetYear}년 ${scopeLabel}`);
        $('#synthesisBadge').text('합성 중...').removeClass('done');
        $('#copyReportBtn').prop('disabled', true);

        try {
            const res = await fetch('/rag/report', {
                method: 'POST',
                headers: { ...authHeader(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ target_year: targetYear, scope }),
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || '보고서 생성 요청 실패');
            }

            // SSE 스트리밍 처리
            const reader  = res.body.getReader();
            const decoder = new TextDecoder();
            let buf = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buf += decoder.decode(value, { stream: true });

                const lines = buf.split('\n');
                buf = lines.pop(); // 미완성 라인 보존

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    let data;
                    try { data = JSON.parse(line.slice(6)); } catch { continue; }
                    handleSseEvent(data);
                }
            }

        } catch (e) {
            showError(e.message);
        } finally {
            isGenerating = false;
            $btn.prop('disabled', false).find('span:last').text('🚀');
        }
    });

    /* ──────────────────────────────────────
       7. SSE 이벤트 핸들러
    ────────────────────────────────────── */
    function handleSseEvent(data) {
        switch (data.type) {

            // plan: 섹션 목록 확정
            case 'plan':
                handlePlan(data.sections || []);
                break;

            // section_start: 특정 섹션 처리 시작
            case 'section_start':
                handleSectionStart(data.section_title, data.esg_category);
                break;

            // section_done: 특정 섹션 초안 완료
            case 'section_done':
                handleSectionDone(data);
                break;

            // synthesis: 최종 합성 시작 신호
            case 'synthesis':
                handleSynthesisStart();
                break;

            // text: 최종 보고서 텍스트 청크 (fake-stream)
            case 'text':
                appendFinalReportChunk(data.chunk || '');
                break;

            // done: 전체 완료
            case 'done':
                handleDone();
                break;

            // error: 오류
            case 'error':
                showError(data.message || '알 수 없는 오류');
                break;
        }
    }

    /* ── plan 처리 ── */
    function handlePlan(sections) {
        totalSections  = sections.length;
        completedCount = 0;
        updateProgress(2);
        updateSectionCounter();

        // 섹션 카드 생성 (모두 pending 상태)
        const $grid = $('#sectionsGrid').empty();
        sections.forEach((s, idx) => {
            $grid.append(buildSectionCard(s.title, s.category, idx));
        });

        $('#sectionsPanel').show();
        // 스크롤을 섹션 패널로 이동
        $('.report-scroll-area').scrollTop(0);

        // 생성된 결과물 화면저장
        saveReportState();
    }

    /* ── section_start 처리 ── */
    function handleSectionStart(title) {
        const $card = findCard(title);
        if (!$card.length) return;
        $card.removeClass('pending').addClass('processing');
        $card.find('.section-status-icon').text('⚙️');
        $card.find('.section-processing-msg').show();
    }

    /* ── section_done 처리 ── */
    function handleSectionDone(data) {
        completedCount++;
        updateSectionCounter();

        const $card = findCard(data.section_title);
        if (!$card.length) return;

        $card.removeClass('processing').addClass('done');
        $card.find('.section-status-icon').text('✅');
        $card.find('.section-processing-msg').hide();

        // 초안 텍스트 삽입
        $card.find('.section-draft-text').text(data.draft || '');

        // 복사 버튼 이벤트
        const draft = data.draft || '';
        $card.find('.copy-section-btn').off('click').on('click', function () {
            const $btn = $(this);
            // navigator.clipboard.writeText(draft)
            //     .then(() => {
            //         $btn.text('✅ 복사됨');
            //         setTimeout(() => $btn.text('📋 복사'), 1500);
            //     })
            //     .catch(() => alert('클립보드 복사 실패'));
            copyText(draft) ? $btn.text('✅ 복사됨') : alert('클립보드 복사 실패');
            setTimeout(() => $btn.text('📋 복사'), 1500);
        });

        // 소스 패널 삽입
        if (data.sources && data.sources.length) {
            $card.find('.section-sources-holder')
                .empty()
                .append(buildSourcesPanel(data.sources, false));
        }

        $card.find('.section-done-content').show();

        // 모든 섹션 완료 시 step2 완료 표시 (synthesis 이벤트 전 표시용)
        if (completedCount >= totalSections) {
            $('#pStep2').addClass('completed').removeClass('active');
        }

        // 생성된 결과물 화면저장
        saveReportState();
    }

    /* ── synthesis 처리 ── */
    function handleSynthesisStart() {
        updateProgress(3);
        // 최종 보고서 패널 표시 + 로딩 메시지
        $('#finalReportContent').html(`
            <div class="synthesis-loading-msg">
                <div class="mini-spinner"></div>
                <span>섹션 초안을 취합하여 최종 보고서를 생성하는 중입니다...</span>
            </div>`);
        $('#finalReportPanel').show();
        // 패널로 스크롤
        setTimeout(() => {
            const el = document.getElementById('finalReportPanel');
            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 300);

        // 생성된 결과물 화면저장
        saveReportState();
    }

    /* ── text 청크 처리 (최종 보고서 스트리밍) ── */
    function appendFinalReportChunk(chunk) {
        finalReportText += chunk;
        const $content = $('#finalReportContent');
        // 최초 청크 도착 시 로딩 메시지 제거
        if ($content.find('.synthesis-loading-msg').length) {
            $content.empty();
        }
        $content.text(finalReportText);
        // 아래로 자동 스크롤
        const area = document.querySelector('.report-scroll-area');
        if (area) area.scrollTop = area.scrollHeight;
    }

    /* ── done 처리 ── */
    function handleDone() {
        updateProgress(4);
        $('#synthesisBadge').text('✅ 합성 완료').addClass('done');
        $('#copyReportBtn').prop('disabled', false);

        // 복사 버튼 이벤트
        $('#copyReportBtn').off('click').on('click', function () {
            const $btn = $(this);
            // navigator.clipboard.writeText(finalReportText)
            //     .then(() => {
            //         $btn.text('✅ 복사됨');
            //         setTimeout(() => $btn.text('📋 전체 복사'), 1600);
            //     })
            //     .catch(() => alert('클립보드 복사 실패'));
            copyText(finalReportText) ? $btn.text('✅ 복사됨') : alert('클립보드 복사 실패');
            setTimeout(() => $btn.text('📋 전체 복사'), 1600);
        });

        // 생성된 결과물 화면저장
        saveReportState();
    }

    /* ── 오류 표시 ── */
    function showError(msg) {
        updateProgress(0);
        $('#errorMessage').text(msg);
        $('#errorPanel').show();
        // 진행 바 숨기기
        $('#progressBar').hide();
        // empty state 복원
        if (!$('#sectionsPanel').is(':visible') && !$('#finalReportPanel').is(':visible')) {
            $('#emptyState').show();
        }
    }

    /* ──────────────────────────────────────
       8. 섹션 카드 빌더
    ────────────────────────────────────── */
    function buildSectionCard(title, category, idx) {
        const cat   = (category || 'I').toUpperCase();
        const catCls = ['I','E','S','G'].includes(cat) ? cat : 'I';

        return $(`
            <div class="section-card pending" data-title="${escHtml(title)}" data-idx="${idx}">
                <div class="section-card-header">
                    <span class="cat-badge ${catCls}">${catCls}</span>
                    <span class="section-card-title" title="${escHtml(title)}">${escHtml(title)}</span>
                    <span class="section-status-icon">⏳</span>
                </div>
                <div class="section-card-body">
                    <!-- 처리 중 메시지 -->
                    <div class="section-processing-msg" style="display:none;">
                        <div class="mini-spinner"></div>
                        <span>초안 생성 중...</span>
                    </div>
                    <!-- 완료 콘텐츠 -->
                    <div class="section-done-content" style="display:none;">
                        <div class="section-draft-toggle">
                            <span>초안 보기</span>
                            <span class="draft-toggle-arrow">▶</span>
                        </div>
                        <div class="section-draft-body" style="display:none;">
                            <pre class="section-draft-text"></pre>
                            <button class="copy-section-btn">📋 복사</button>
                        </div>
                        <div class="section-sources-holder" style="margin-top:8px;"></div>
                    </div>
                </div>
            </div>`);
    }

    /* 초안 토글 이벤트 (동적 바인딩) */
    $(document).on('click', '.section-draft-toggle', function () {
        const $body  = $(this).next('.section-draft-body');
        const $arrow = $(this).find('.draft-toggle-arrow');
        const isOpen = $body.is(':visible');
        $body.slideToggle(150);
        $arrow.text(isOpen ? '▶' : '▼');
    });

    /* 섹션 패널 전체 토글 (헤더 클릭 → 그리드 접기/펼치기) */
    $('#sectionsPanelHeader').on('click', function () {
        const $grid  = $('#sectionsGrid');
        const $arrow = $('#sectionsToggleArrow');
        const isOpen = $grid.is(':visible');
        $grid.slideToggle(150);
        $arrow.text(isOpen ? '▶' : '▼');
    });

    /* 카드 찾기: data-title 속성 기반 */
    function findCard(title) {
        // jQuery attribute selector에서 특수문자 이스케이프
        const escaped = title.replace(/[!"#$%&'()*+,.\/:;<=>?@[\\\]^`{|}~]/g, '\\$&');
        return $(`.section-card[data-title="${escaped}"]`);
    }

    /* ──────────────────────────────────────
       9. 소스 패널 빌더 (page4/page5와 동일)
    ────────────────────────────────────── */
    function buildSourcesPanel(sources, expanded) {
        const items = sources.map((s, idx) => {
            const cls =
                s.type === '보도자료'  ? 'press'     :
                s.type === '뉴스룸'    ? 'newsroom'  :
                s.type === 'ESG보고서' ? 'esgreport' :
                s.type === 'ESG데이터' ? 'esgdata'   : '';

            const score = ((1 - (s.distance || 0)) * 100).toFixed(0);

            const titleEl =
                s.type === 'ESG보고서'
                    ? `<span class="source-heading-l1">${escHtml(s.heading_level_1 || '')}</span>`
                : s.type === 'ESG데이터'
                    ? `<span class="source-category-l1">${escHtml(s.category_level_1 || '')}</span>`
                : s.url
                    ? `<a class="source-title-link" href="${escHtml(s.url)}" target="_blank">${escHtml(s.title || '')}</a>`
                    : `<span class="source-title">${escHtml(s.title || '')}</span>`;

            const esgEl     = s.esg_category ? `<span class="source-esg-category">ESG-${escHtml(String(s.esg_category))}</span>` : '';
            const pageEl    = s.page_num     ? `<span class="source-page">p.${escHtml(String(s.page_num))}</span>`        : '';
            const articleEl = s.article_num  ? `<span class="source-page">a.${escHtml(String(s.article_num))}</span>`     : '';
            const chunkEl   = (s.chunk_index !== '' && s.chunk_index != null)
                ? `<span class="source-chunk">c.${escHtml(String(s.chunk_index))}</span>` : '';
            const dateEl    = s.date ? `<span class="source-date">${escHtml(String(s.date))}</span>` : '';

            return `<div class="source-item">
                        <span class="source-num">[${idx + 1}]</span>
                        <span class="source-badge ${cls}">${escHtml(s.type)}</span>
                        ${titleEl}${esgEl}${pageEl}${articleEl}${chunkEl}${dateEl}
                        <span class="source-score">${score}%</span>
                    </div>`;
        }).join('');

        const arrowInit = expanded ? '▼' : '▶';
        const $panel = $(`
            <div class="sources-panel">
              <div class="sources-toggle">
                <span>📎 참고 (${sources.length}건)</span>
                <span class="toggle-arrow">${arrowInit}</span>
              </div>
              <div class="sources-body" style="${expanded ? '' : 'display:none;'}">${items}</div>
            </div>`);

        $panel.find('.sources-toggle').on('click', function () {
            const $body  = $(this).next('.sources-body');
            const $arrow = $(this).find('.toggle-arrow');
            const isOpen = $body.is(':visible');
            $body.toggle(!isOpen);
            $arrow.text(isOpen ? '▶' : '▼');
        });

        return $panel;
    }

    /* ── 초기 로드 ── */
    loadStatus();
    restoreReportState(); // 페이지 로드시 생성되었던 결과물 복원
});