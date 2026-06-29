$(document).ready(function () {

    /* ──────────────────────────────────────
       1. 인증 / 토큰 타이머
    ────────────────────────────────────── */
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

    let timerInterval = setInterval(function () {
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

    function formatDate(isoStr) {
        if (!isoStr) return '';
        const d = new Date(isoStr);
        return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`;
    }

    /* ──────────────────────────────────────
       3. 앱 상태
    ────────────────────────────────────── */
    let currentThreadId = null; // 진행 중인 HITL 스레드 ID
    let currentDraftId  = null; // 선택된 완료 초안 MySQL ID
    let isProcessing    = false;

    // 완료된 초안 데이터 캐시 (draft_id → draft 객체)
    const draftCache = new Map();

    /* ──────────────────────────────────────
       4. localStorage — 진행 중 스레드 관리
    ────────────────────────────────────── */
    const THREADS_KEY = 'section_threads_v1';

    function getStoredThreads() {
        try { return JSON.parse(localStorage.getItem(THREADS_KEY) || '[]'); }
        catch { return []; }
    }
    function saveStoredThread(thread) {
        const list = getStoredThreads().filter(t => t.id !== thread.id);
        list.unshift(thread);
        localStorage.setItem(THREADS_KEY, JSON.stringify(list.slice(0, 20)));
    }
    function updateStoredThread(threadId, patch) {
        const list = getStoredThreads().map(t => t.id === threadId ? { ...t, ...patch } : t);
        localStorage.setItem(THREADS_KEY, JSON.stringify(list));
    }
    function removeStoredThread(threadId) {
        const list = getStoredThreads().filter(t => t.id !== threadId);
        localStorage.setItem(THREADS_KEY, JSON.stringify(list));
    }

    /* ── 진행 중 스레드 목록 렌더링 (done 상태 제외) ── */
    function renderThreadsList() {
        const $el = $('#threadsList').empty();

        // 기존 localStorage에 남은 done 항목 정리
        const all     = getStoredThreads();
        const active  = all.filter(t => t.stage !== 'done');
        if (active.length !== all.length) {
            localStorage.setItem(THREADS_KEY, JSON.stringify(active));
        }

        const STAGE_LABELS = {
            review_docs:  '📎 문서 검토 대기',
            review_draft: '📄 초안 검토 대기',
        };

        if (!active.length) {
            $el.html('<div class="no-threads">진행 중인 작업이 없습니다</div>');
            return;
        }
        active.forEach(t => {
            const isActive   = (t.id === currentThreadId);
            const stageLabel = STAGE_LABELS[t.stage] || t.stage;
            $el.append(`
                <div class="thread-item ${isActive ? 'active' : ''}" data-id="${escHtml(t.id)}">
                    <div class="thread-info">
                        <div class="thread-title">${escHtml(t.title)}</div>
                        <div class="thread-stage">${stageLabel}</div>
                    </div>
                    <button class="thread-delete-btn" data-id="${escHtml(t.id)}" title="목록에서 제거">✕</button>
                </div>`);
        });
    }

    $(document).on('click', '.thread-item', async function (e) {
        if ($(e.target).is('.thread-delete-btn')) return;
        const threadId = $(this).data('id');
        if (threadId === currentThreadId) return;
        await loadThread(threadId);
    });

    $(document).on('click', '.thread-delete-btn', function (e) {
        e.stopPropagation();
        const threadId = $(this).data('id');
        if (!confirm('목록에서 이 작업을 제거하시겠습니까?\n(서버 체크포인트는 유지됩니다)')) return;
        removeStoredThread(threadId);
        if (currentThreadId === threadId) { currentThreadId = null; resetToEmpty(); }
        renderThreadsList();
    });

    async function loadThread(threadId) {
        showLoading('상태 로딩 중...');
        try {
            const res = await fetch(`/rag/sections/${threadId}`, { headers: authHeader() });
            if (!res.ok) {
                if (res.status === 404) {
                    removeStoredThread(threadId);
                    renderThreadsList();
                    hideLoading();
                    alert('서버에서 해당 작업을 찾을 수 없습니다.\n(서버 재시작 후 체크포인트가 초기화되었을 수 있습니다)');
                    return;
                }
                throw new Error((await res.json().catch(() => ({}))).detail || '로드 실패');
            }
            const data = await res.json();
            currentThreadId = threadId;
            currentDraftId  = null;
            setActiveThread(threadId);
            renderStage(data);
        } catch (e) {
            hideLoading();
            alert('오류: ' + e.message);
        }
    }

    /* ──────────────────────────────────────
       5. MySQL — 완료된 초안 목록 관리
    ────────────────────────────────────── */
    async function loadCompletedDrafts() {
        try {
            const res = await fetch('/rag/sections/drafts', { headers: authHeader() });
            if (!res.ok) throw new Error('목록 로드 실패');
            const data = await res.json();
            draftCache.clear();
            (data.drafts || []).forEach(d => draftCache.set(d.id, d));
            renderDraftsList(data.drafts || []);
        } catch (e) {
            $('#draftsList').html('<div class="no-drafts" style="color:#e57373;">로드 실패</div>');
        }
    }

    function renderDraftsList(drafts) {
        const $el = $('#draftsList').empty();
        if (!drafts.length) {
            $el.html('<div class="no-drafts">완료된 초안이 없습니다</div>');
            return;
        }
        drafts.forEach(d => {
            const isActive = (d.id === currentDraftId);
            const cat      = d.esg_category || 'I';
            const catChip  = `<span class="cat-chip ${escHtml(cat)}">${escHtml(cat)}</span>`;
            $el.append(`
                <div class="draft-list-item ${isActive ? 'active' : ''}" data-draft-id="${d.id}">
                    <div class="draft-list-info">
                        <div class="draft-list-title">${escHtml(d.section_title)}</div>
                        <div class="draft-list-meta">${catChip}${escHtml(d.target_year)} · ${escHtml(formatDate(d.created_at))}</div>
                    </div>
                    <button class="draft-delete-btn" data-draft-id="${d.id}" title="영구 삭제">✕</button>
                </div>`);
        });
    }

    /* 완료된 초안 클릭 → done 패널 렌더링 */
    $(document).on('click', '.draft-list-item', function (e) {
        if ($(e.target).is('.draft-delete-btn')) return;
        const draftId   = parseInt($(this).data('draft-id'));
        if (draftId === currentDraftId) return;
        const draftData = draftCache.get(draftId);
        if (!draftData) return;
        currentThreadId = null;
        currentDraftId  = draftId;
        setActiveDraft(draftId);
        updateProgress(5);
        const $c = $('#stageContainer').empty();
        renderDone($c, { draft: draftData.draft, sources: draftData.sources });
        $('.stage-scroll-area').scrollTop(0);
    });

    /* 완료된 초안 삭제 → DELETE /rag/sections/drafts/{id} */
    $(document).on('click', '.draft-delete-btn', async function (e) {
        e.stopPropagation();
        const draftId = parseInt($(this).data('draft-id'));
        if (!confirm('이 초안을 영구 삭제하시겠습니까?\n(복구할 수 없습니다)')) return;
        try {
            const res = await fetch(`/rag/sections/drafts/${draftId}`, {
                method: 'DELETE',
                headers: authHeader(),
            });
            if (!res.ok && res.status !== 204) {
                throw new Error((await res.json().catch(() => ({}))).detail || '삭제 실패');
            }
            if (currentDraftId === draftId) { currentDraftId = null; resetToEmpty(); }
            await loadCompletedDrafts();
        } catch (e) {
            alert('오류: ' + e.message);
        }
    });

    /* 새로고침 버튼 */
    $('#refreshDraftsBtn').on('click', () => loadCompletedDrafts());

    /* 활성 상태 표시 헬퍼 */
    function setActiveThread(threadId) {
        $('.thread-item, .draft-list-item').removeClass('active');
        $(`.thread-item[data-id="${threadId}"]`).addClass('active');
    }
    function setActiveDraft(draftId) {
        $('.thread-item, .draft-list-item').removeClass('active');
        $(`.draft-list-item[data-draft-id="${draftId}"]`).addClass('active');
    }

    /* ──────────────────────────────────────
       6. 로딩 오버레이
    ────────────────────────────────────── */
    function showLoading(message) {
        isProcessing = true;
        $('#startBtn').prop('disabled', true);
        $('#loadingMessage').text(message || '처리 중...');
        $('#loadingOverlay').stop(true).fadeIn(150);
    }
    function hideLoading() {
        isProcessing = false;
        $('#startBtn').prop('disabled', false);
        $('#loadingOverlay').stop(true).fadeOut(150);
    }

    /* ──────────────────────────────────────
       7. 진행 단계 바
    ────────────────────────────────────── */
    function updateProgress(step) {
        for (let i = 1; i <= 5; i++) {
            const $s = $(`#pStep${i}`);
            $s.removeClass('active completed');
            if (i < step)        $s.addClass('completed');
            else if (i === step) $s.addClass('active');
        }
    }

    /* ──────────────────────────────────────
       8. 새 섹션 작성 시작
    ────────────────────────────────────── */
    $('#startBtn').on('click', async function () {
        if (isProcessing) return;
        const sectionTitle = $('#sectionTitle').val().trim();
        const targetYear   = $('#targetYear').val();
        const esgCategory  = $('#esgCategory').val() || null;
        const userQuery    = $('#userQuery').val().trim();

        if (!sectionTitle) { alert('섹션 제목을 입력하세요'); return; }
        if (!userQuery)    { alert('작성 요청 내용을 입력하세요'); return; }

        updateProgress(1);
        showLoading('문서 검색 및 평가 중...');

        try {
            const res = await fetch('/rag/sections', {
                method: 'POST',
                headers: { ...authHeader(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ section_title: sectionTitle, target_year: targetYear, esg_category: esgCategory, user_query: userQuery }),
            });
            if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || '섹션 시작 실패');
            const data = await res.json();
            currentThreadId = data.thread_id;
            currentDraftId  = null;
            saveStoredThread({ id: data.thread_id, title: `${sectionTitle} (${targetYear})`, stage: data.stage, created: new Date().toISOString() });
            renderThreadsList();
            setActiveThread(data.thread_id);
            renderStage(data);
        } catch (e) {
            hideLoading();
            updateProgress(0);
            alert('오류: ' + e.message);
        }
    });

    /* ──────────────────────────────────────
       9. HITL 피드백 전송
    ────────────────────────────────────── */
    const FEEDBACK_CONFIG = {
        approve: { message: '초안 생성 중...',         step: 3 },
        reject:  { message: '재검색 및 평가 중...',    step: 1 },
        edit:    { message: '초안 수정 중...',          step: 3 },
        search:  { message: '추가 검색 및 평가 중...', step: 1 },
    };

    async function sendFeedback(action, content) {
        if (!currentThreadId || isProcessing) return;
        const cfg = FEEDBACK_CONFIG[action] || { message: '처리 중...', step: 1 };
        updateProgress(cfg.step);
        showLoading(cfg.message);

        try {
            const res = await fetch(`/rag/sections/${currentThreadId}/feedback`, {
                method: 'POST',
                headers: { ...authHeader(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, content: content || null }),
            });
            if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || '피드백 전송 실패');
            const data = await res.json();

            if (data.stage === 'done') {
                // done: localStorage 제거 + MySQL 목록 갱신 + 방금 저장된 항목 활성화
                const finishedId = currentThreadId;
                removeStoredThread(finishedId);
                renderThreadsList();
                currentThreadId = null;

                await loadCompletedDrafts(); // 목록 새로고침

                // thread_id 매칭으로 신규 저장 항목 찾기
                for (const [id, d] of draftCache.entries()) {
                    if (d.thread_id === finishedId) {
                        currentDraftId = id;
                        setActiveDraft(id);
                        break;
                    }
                }
                renderStage(data);
            } else {
                updateStoredThread(currentThreadId, { stage: data.stage });
                renderThreadsList();
                setActiveThread(currentThreadId);
                renderStage(data);
            }
        } catch (e) {
            hideLoading();
            alert('오류: ' + e.message);
        }
    }

    /* ──────────────────────────────────────
       10. 스테이지 렌더링
    ────────────────────────────────────── */
    const STAGE_TO_STEP = { review_docs: 2, review_draft: 4, done: 5 };

    function renderStage(data) {
        hideLoading();
        updateProgress(STAGE_TO_STEP[data.stage] || 2);
        const $c = $('#stageContainer').empty();
        if      (data.stage === 'review_docs')  renderReviewDocs($c, data);
        else if (data.stage === 'review_draft') renderReviewDraft($c, data);
        else if (data.stage === 'done')         renderDone($c, data);
        else $c.html(`<div class="empty-state"><p>알 수 없는 단계: ${escHtml(data.stage)}</p></div>`);
        $('.stage-scroll-area').scrollTop(0);
    }

    function resetToEmpty() {
        updateProgress(0);
        $('#stageContainer').html(`
            <div class="empty-state">
                <h2>섹션 작성 워크플로우</h2>
                <p>좌측 폼에서 섹션 정보를 입력하거나 완료된 초안을 선택하세요</p>
            </div>`);
    }

    /* ── review_docs ── */
    function renderReviewDocs($c, data) {
        const sources = data.sources || [];
        const $panel  = $('<div class="stage-panel review-docs-panel"></div>').appendTo($c);
        $panel.append(`
            <div class="stage-header">
                <span class="stage-badge docs">HITL 1</span>
                <span class="stage-title">📎 검색 문서 검토</span>
            </div>
            <div class="stage-message">${escHtml(data.message || '검색된 문서를 검토하세요.')}</div>`);
        if (sources.length) $panel.append(buildSourcesPanel(sources, true));
        else $panel.append('<div class="no-sources">검색된 문서가 없습니다</div>');
        const $a = $('<div class="action-panel"></div>').appendTo($panel);
        $a.append(`
            <div class="action-group">
                <button class="action-approve-btn" id="approveDocsBtn">✅ 승인 — 이 문서들로 초안 생성 진행</button>
            </div>
            <div class="action-group">
                <div class="action-group-label">🔄 반려 — 다른 키워드로 재검색</div>
                <div class="action-input-row">
                    <input type="text" class="action-input" id="rejectQueryInput" placeholder="새 검색어를 입력하세요">
                    <button class="action-reject-btn" id="rejectDocsBtn">재검색</button>
                </div>
            </div>`);
        $('#approveDocsBtn').on('click', () => sendFeedback('approve', null));
        $('#rejectDocsBtn').on('click', () => {
            const q = $('#rejectQueryInput').val().trim();
            if (!q) { alert('재검색할 키워드를 입력하세요'); return; }
            sendFeedback('reject', q);
        });
        $('#rejectQueryInput').on('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('#rejectDocsBtn').trigger('click'); } });
    }

    /* ── review_draft ── */
    function renderReviewDraft($c, data) {
        const sources = data.sources || [];
        const draft   = data.draft   || '';
        const $panel  = $('<div class="stage-panel review-draft-panel"></div>').appendTo($c);
        $panel.append(`
            <div class="stage-header">
                <span class="stage-badge draft">HITL 2</span>
                <span class="stage-title">📄 초안 검토</span>
            </div>
            <div class="stage-message">${escHtml(data.message || '초안을 검토하세요.')}</div>`);
        const $dp = $('<div class="draft-panel"></div>').appendTo($panel);
        $dp.append(`
            <div class="draft-header">
                <span>생성된 초안</span>
                <button class="draft-copy-btn" id="draftCopyBtn">📋 복사</button>
            </div>
            <pre class="draft-text">${escHtml(draft)}</pre>`);
        if (sources.length) $panel.append(buildSourcesPanel(sources, false));
        const $a = $('<div class="action-panel"></div>').appendTo($panel);
        $a.append(`
            <div class="action-group">
                <button class="action-approve-btn" id="approveDraftBtn">✅ 최종 승인 — 작성 완료 및 저장</button>
            </div>
            <div class="action-group">
                <div class="action-group-label">✏️ 수정 요청 — 지시에 따라 초안 재작성</div>
                <div class="action-input-col">
                    <textarea class="action-textarea" id="editInstructionInput" rows="3"
                              placeholder="예: 3번째 문단에 수자원 관리 내용 추가해줘"></textarea>
                    <button class="action-edit-btn" id="editDraftBtn">수정 요청</button>
                </div>
            </div>
            <div class="action-group">
                <div class="action-group-label">🔍 추가 검색 — 보강 자료 검색 후 초안 재작성</div>
                <div class="action-input-row">
                    <input type="text" class="action-input" id="searchKeywordInput" placeholder="예: 재생에너지 도입률 2024">
                    <button class="action-search-btn" id="searchDraftBtn">검색 추가</button>
                </div>
            </div>`);
        $('#approveDraftBtn').on('click', () => sendFeedback('approve', null));
        $('#editDraftBtn').on('click', () => {
            const ins = $('#editInstructionInput').val().trim();
            if (!ins) { alert('수정 지시 내용을 입력하세요'); return; }
            sendFeedback('edit', ins);
        });
        $('#searchDraftBtn').on('click', () => {
            const kw = $('#searchKeywordInput').val().trim();
            if (!kw) { alert('추가 검색 키워드를 입력하세요'); return; }
            sendFeedback('search', kw);
        });
        $('#searchKeywordInput').on('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('#searchDraftBtn').trigger('click'); } });
        $('#draftCopyBtn').on('click', function () {
            const $b = $(this);
            navigator.clipboard.writeText(draft)
                .then(() => { $b.text('✅ 복사됨'); setTimeout(() => $b.text('📋 복사'), 1600); })
                .catch(() => alert('클립보드 복사 실패'));
        });
    }

    /* ── done 패널 (HITL 완료 후 / 완료 초안 선택 시 공용) ── */
    function renderDone($c, data) {
        const sources = data.sources || [];
        const draft   = data.draft   || '';
        const $panel  = $('<div class="stage-panel done-panel"></div>').appendTo($c);
        $panel.append(`
            <div class="stage-header">
                <span class="stage-badge done">완료</span>
                <span class="stage-title">✅ 섹션 작성 완료</span>
            </div>
            <div class="stage-message">초안이 완료되어 DB에 저장되었습니다. 좌측 '완료된 초안' 목록에서 다시 불러올 수 있습니다.</div>`);
        const $dp = $('<div class="draft-panel final"></div>').appendTo($panel);
        $dp.append(`
            <div class="draft-header">
                <span>최종 초안</span>
                <div class="done-actions-row">
                    <button class="draft-copy-btn" id="finalCopyBtn">📋 전체 복사</button>
                    <button class="new-section-btn" id="newSectionBtn">+ 새 섹션</button>
                </div>
            </div>
            <pre class="draft-text">${escHtml(draft)}</pre>`);
        if (sources.length) $panel.append(buildSourcesPanel(sources, false));
        $('#finalCopyBtn').on('click', function () {
            const $b = $(this);
            navigator.clipboard.writeText(draft)
                .then(() => { $b.text('✅ 복사됨'); setTimeout(() => $b.text('📋 전체 복사'), 1600); })
                .catch(() => alert('클립보드 복사 실패'));
        });
        $('#newSectionBtn').on('click', () => {
            currentThreadId = null;
            currentDraftId  = null;
            $('.thread-item, .draft-list-item').removeClass('active');
            resetToEmpty();
            $('#sectionTitle').focus();
        });
    }

    /* ──────────────────────────────────────
       11. 소스 패널 빌더
    ────────────────────────────────────── */
    function buildSourcesPanel(sources, expanded) {
        const items = sources.map((s, idx) => {
            const cls =
                s.type === '보도자료'  ? 'press'     :
                s.type === '뉴스룸'    ? 'newsroom'  :
                s.type === 'ESG보고서' ? 'esgreport' :
                s.type === 'ESG데이터' ? 'esgdata'   : '';
            const score  = ((1 - (s.distance || 0)) * 100).toFixed(0);
            const titleEl =
                s.type === 'ESG보고서'  ? `<span class="source-heading-l1">${escHtml(s.heading_level_1 || '')}</span>` :
                s.type === 'ESG데이터'  ? `<span class="source-category-l1">${escHtml(s.category_level_1 || '')}</span>` :
                s.url ? `<a class="source-title-link" href="${escHtml(s.url)}" target="_blank">${escHtml(s.title || '')}</a>` :
                        `<span class="source-title">${escHtml(s.title || '')}</span>`;
            const esgEl = s.esg_category ? `<span class="source-esg-category">ESG-${escHtml(String(s.esg_category))}</span>` : '';
            const pgEl  = s.page_num    ? `<span class="source-page">p.${escHtml(String(s.page_num))}</span>` : '';
            const artEl = s.article_num ? `<span class="source-page">a.${escHtml(String(s.article_num))}</span>` : '';
            const chkEl = (s.chunk_index !== '' && s.chunk_index != null) ? `<span class="source-chunk">c.${escHtml(String(s.chunk_index))}</span>` : '';
            const dtEl  = s.date        ? `<span class="source-date">${escHtml(String(s.date))}</span>` : '';
            return `<div class="source-item"><span class="source-num">[${idx + 1}]</span><span class="source-badge ${cls}">${escHtml(s.type)}</span>${titleEl}${esgEl}${pgEl}${artEl}${chkEl}${dtEl}<span class="source-score">${score}%</span></div>`;
        }).join('');
        const $p = $(`
            <div class="sources-panel">
              <div class="sources-toggle">
                <span>📎 참고 문서 (${sources.length}개)</span>
                <span class="toggle-arrow">${expanded ? '▼' : '▶'}</span>
              </div>
              <div class="sources-body" style="${expanded ? '' : 'display:none;'}">${items}</div>
            </div>`);
        $p.find('.sources-toggle').on('click', function () {
            const $b = $(this).next('.sources-body');
            $b.toggle(!$b.is(':visible'));
            $(this).find('.toggle-arrow').text($b.is(':visible') ? '▼' : '▶');
        });
        return $p;
    }

    /* ── 초기 로드 ── */
    renderThreadsList();
    loadCompletedDrafts();
});