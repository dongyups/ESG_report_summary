$(document).ready(function () {

    /* ──────────────────────────────────────
       1. 인증 / 타이머
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
       2. 인덱싱 상태 조회
    ────────────────────────────────────── */
    async function loadStatus() {
        try {
            const res = await fetch('/rag/status', { headers: authHeader() });
            if (!res.ok) return;
            const data = await res.json();
            $('#pressCount').text(data.sk_hynix_press ?? 0);
            $('#newsroomCount').text(data.sk_hynix_newsroom ?? 0);
            $('#reportCount').text(data.sk_hynix_report ?? 0);
            $('#esgdataCount').text(data.sk_hynix_esg_data ?? 0);
        } catch (e) { console.error(e); }
    }

    /* ──────────────────────────────────────
       3. 인덱싱 실행
    ────────────────────────────────────── */
    $('#indexBtn').on('click', async function () {
        const $btn  = $(this);
        const $prog = $('#indexProgress');

        $btn.prop('disabled', true).text('인덱싱 중...');
        $prog.show().html('');

        try {
            const res = await fetch('/rag/index', {
                method: 'POST',
                headers: authHeader(),
            });
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
                    $prog.append(`<div>${d.message}</div>`);
                    if (d.done) { await loadStatus(); }
                }
            }
        } catch (e) {
            $prog.append(`<div style="color:red">오류: ${e.message}</div>`);
        } finally {
            $btn.prop('disabled', false).text('⟳ ChromaDB 인덱싱 실행');
        }
    });

    /* ──────────────────────────────────────
       4. 대화 목록
    ────────────────────────────────────── */
    let currentConvId = null;
    let editingConvId = null;
    let isStreaming   = false;

    async function loadConversations() {
        try {
            const res = await fetch('/rag/conversations', { headers: authHeader() });
            if (!res.ok) return;
            renderConversations(await res.json());
        } catch (e) { console.error(e); }
    }

    function renderConversations(list) {
        const $el = $('#conversationsList').empty();
        if (!list.length) {
            $el.html('<div style="padding:20px;text-align:center;color:#999;font-size:13px;">대화가 없습니다</div>');
            return;
        }
        list.forEach(c => {
            $el.append(`
                <div class="conversation-item ${c.id === currentConvId ? 'active' : ''}" data-id="${c.id}">
                    <div class="conversation-title">${escHtml(c.title)}</div>
                    <div class="conversation-actions">
                        <button class="action-btn edit-btn"   data-id="${c.id}">✏️</button>
                        <button class="action-btn delete-btn" data-id="${c.id}">🗑️</button>
                    </div>
                </div>`);
        });
    }

    $(document).on('click', '.conversation-item', function (e) {
        if ($(e.target).closest('.action-btn').length) return;
        loadConversation($(this).data('id'));
    });

    $('#newChatBtn').on('click', async function () {
        try {
            const res  = await fetch('/rag/conversations', {
                method: 'POST',
                headers: { ...authHeader(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: '새 RAG 채팅' }),
            });
            const conv = await res.json();
            currentConvId = conv.id;
            await loadConversations();
            clearMessages();
        } catch (e) { alert('새 채팅 생성 실패'); }
    });

    async function loadConversation(id) {
        try {
            const res  = await fetch(`/rag/conversations/${id}`, { headers: authHeader() });
            const conv = await res.json();
            currentConvId = id;
            renderMessages(conv.messages);
            $('.conversation-item').removeClass('active');
            $(`.conversation-item[data-id="${id}"]`).addClass('active');
        } catch (e) { alert('대화 로드 실패'); }
    }

    /* ──────────────────────────────────────
       5. 메시지 렌더링 (로드 시)
    ────────────────────────────────────── */
    function renderMessages(msgs) {
        const $w = $('#messagesWrapper').empty();
        if (!msgs.length) { $w.html(emptyStateHtml()); return; }
        msgs.forEach(m => {
            if (m.role === 'user') {
                addUserMsgToUI(m.content);
            } else {
                const sources  = m.sources  ? JSON.parse(m.sources)  : [];
                const thinking = m.thinking || null;
                addAssistantMsgToUI(m.content, thinking, sources);
            }
        });
        scrollBottom();
    }

    /* ──────────────────────────────────────
       6. 메시지 전송
    ────────────────────────────────────── */
    $('#sendBtn').on('click', sendMessage);
    $('#messageInput').on('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });

    async function sendMessage() {
        if (isStreaming) return;
        const content = $('#messageInput').val().trim();
        if (!content) return;

        if (!currentConvId) {
            await $('#newChatBtn').trigger('click');
            await new Promise(r => setTimeout(r, 300));
        }
        if (!currentConvId) { alert('채팅 생성 실패'); return; }

        addUserMsgToUI(content);
        $('#messageInput').val('').css('height', 'auto');
        isStreaming = true;
        $('#sendBtn').prop('disabled', true);

        // 빈 assistant 메시지 생성
        const $msg = $('<div class="message assistant"></div>').appendTo('#messagesWrapper');
        $msg.append('<div class="message-role">AI 어시스턴트 (RAG)</div>');

        // thinking 로딩 패널
        const $thinkLoad = $(`
            <div class="thinking-panel" id="thinkLoading">
              <div class="thinking-toggle">
                <span class="thinking-icon">💭</span>
                <span class="thinking-label">생각 중...</span>
                <div class="thinking-spinner"></div>
              </div>
            </div>`).appendTo($msg);

        const $content = $('<div class="message-content"></div>').appendTo($msg);
        scrollBottom();

        try {
            const res = await fetch(`/rag/conversations/${currentConvId}/messages`, {
                method: 'POST',
                headers: { ...authHeader(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ content }),
            });
            if (!res.ok) throw new Error('전송 실패');

            const reader  = res.body.getReader();
            const decoder = new TextDecoder();
            let   buf     = '';
            let   fullText = '';
            let   sourcesRendered  = false;
            let   thinkingRendered = false;

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buf += decoder.decode(value, { stream: true });

                const lines = buf.split('\n');
                buf = lines.pop(); // 미완성 라인 보존

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    let parsed;
                    try { parsed = JSON.parse(line.slice(6)); } catch { continue; }

                    switch (parsed.type) {
                        case 'sources':
                            if (!sourcesRendered && parsed.sources?.length) {
                                $thinkLoad.before(buildSourcesPanel(parsed.sources));
                                sourcesRendered = true;
                            }
                            break;

                        case 'thinking_start':
                            // 이미 로딩 패널이 표시 중 — 아무것도 안 해도 됨
                            break;

                        case 'thinking':
                            if (!thinkingRendered) {
                                $thinkLoad.replaceWith(buildThinkingPanel(parsed.content));
                                thinkingRendered = true;
                            }
                            break;

                        case 'text':
                            fullText += parsed.chunk;
                            $content.text(fullText);
                            scrollBottom();
                            break;

                        case 'done':
                            if (!thinkingRendered) $thinkLoad.remove();
                            break;

                        case 'error':
                            $content.text('오류: ' + parsed.message);
                            if (!thinkingRendered) $thinkLoad.remove();
                            break;
                    }
                }
            }

            await loadConversations();

        } catch (e) {
            $content.text('메시지 전송 실패: ' + e.message);
            $thinkLoad.remove();
        } finally {
            isStreaming = false;
            $('#sendBtn').prop('disabled', false);
        }
    }

    /* ──────────────────────────────────────
       7. UI 헬퍼
    ────────────────────────────────────── */
    function addUserMsgToUI(content) {
        $('.empty-state').remove();
        $('#messagesWrapper').append(`
            <div class="message user">
              <div class="message-role">사용자</div>
              <div class="message-content">${escHtml(content)}</div>
            </div>`);
        scrollBottom();
    }

    function addAssistantMsgToUI(content, thinking, sources) {
        $('.empty-state').remove();
        const $msg = $('<div class="message assistant"></div>');
        $msg.append('<div class="message-role">AI 어시스턴트 (RAG)</div>');
        if (sources && sources.length) $msg.append(buildSourcesPanel(sources));
        if (thinking) $msg.append(buildThinkingPanel(thinking));
        $msg.append(`<div class="message-content">${escHtml(content)}</div>`);
        $('#messagesWrapper').append($msg);
        scrollBottom();
    }

    /* thinking 패널 빌더 */
    function buildThinkingPanel(text) {
        const $panel = $(`
            <div class="thinking-panel">
              <div class="thinking-toggle">
                <span class="thinking-icon">💭</span>
                <span class="thinking-label">생각 과정</span>
                <span class="toggle-arrow">▶</span>
              </div>
              <div class="thinking-body" style="display:none;">
                <pre class="thinking-text"></pre>
              </div>
            </div>`);
        $panel.find('.thinking-text').text(text);
        $panel.find('.thinking-toggle').on('click', function () {
            const $body  = $(this).next('.thinking-body');
            const $arrow = $(this).find('.toggle-arrow');
            const open   = $body.is(':visible');
            $body.toggle(!open);
            $arrow.toggleClass('open', !open).text(open ? '▶' : '▼');
        });
        return $panel;
    }

    /* sources 패널 빌더 */
    function buildSourcesPanel(sources) {
        const items = sources.map(s => {
            const cls =
                s.type === '보도자료'  ? 'press' :
                s.type === '뉴스룸'    ? 'newsroom' :
                s.type === 'ESG보고서' ? 'esgreport' :
                s.type === 'ESG데이터' ? 'esgdata' :
                '';
            const score = ((1 - s.distance) * 100).toFixed(0);
            const titleEl =
                s.type === 'ESG보고서'
                    ? `<span class="source-heading-l1">${escHtml(s.heading_level_1 || '')}</span>`
                : s.type === 'ESG데이터'
                    ? `<span class="source-category-l1">${escHtml(s.category_level_1 || '')}</span>`
                : s.url
                    ? `<a class="source-title-link" href="${escHtml(s.url)}" target="_blank">${escHtml(s.title || '')}</a>`
                    : `<span class="source-title">${escHtml(s.title || '')}</span>`;

            const esgEl  = s.esg_category ? `<span class="source-esg-category">ESG-${escHtml(String(s.esg_category))}</span>` : '';
            const pageEl  = s.page_num ? `<span class="source-page">p.${escHtml(String(s.page_num))}</span>` : '';
            const articleEl  = s.article_num ? `<span class="source-page">a.${escHtml(String(s.article_num))}</span>` : '';
            const chunkEl  = s.chunk_index !== '' && s.chunk_index != null ? `<span class="source-chunk">c.${escHtml(String(s.chunk_index))}</span>` : '';
            const dateEl  = s.date ? `<span class="source-date">${escHtml(String(s.date))}</span>` : '';
            return `<div class="source-item">
                      <span class="source-badge ${cls}">${s.type}</span>
                      ${titleEl}${esgEl}${pageEl}${articleEl}${chunkEl}${dateEl}
                      <span class="source-score">${score}%</span>
                    </div>`;
        }).join('');

        const $panel = $(`
            <div class="sources-panel">
              <div class="sources-toggle">
                <span>📎 참고 문서 (${sources.length}개)</span>
                <span class="toggle-arrow">▶</span>
              </div>
              <div class="sources-body" style="display:none;">${items}</div>
            </div>`);

        $panel.find('.sources-toggle').on('click', function () {
            const $body  = $(this).next('.sources-body');
            const $arrow = $(this).find('.toggle-arrow');
            const open   = $body.is(':visible');
            $body.toggle(!open);
            $arrow.toggleClass('open', !open).text(open ? '▶' : '▼');
        });
        return $panel;
    }

    function clearMessages() {
        $('#messagesWrapper').html(emptyStateHtml());
    }
    function emptyStateHtml() {
        return `<div class="empty-state">
                  <h2>메시지를 입력하세요</h2>
                  <p>SK하이닉스 데이터를 기반으로 답변합니다</p>
                </div>`;
    }
    function scrollBottom() {
        const c = document.getElementById('chatContainer');
        c.scrollTop = c.scrollHeight;
    }

    /* ──────────────────────────────────────
       8. 제목 수정 / 삭제
    ────────────────────────────────────── */
    $(document).on('click', '.edit-btn', function (e) {
        e.stopPropagation();
        editingConvId = $(this).data('id');
        $('#editTitleInput').val($(this).closest('.conversation-item').find('.conversation-title').text());
        $('#editModal').addClass('active');
    });
    $('#cancelEditBtn, #editModal').on('click', function (e) {
        if (e.target === this) { $('#editModal').removeClass('active'); editingConvId = null; }
    });
    $('#confirmEditBtn').on('click', async function () {
        const t = $('#editTitleInput').val().trim();
        if (!t) return;
        try {
            await fetch(`/rag/conversations/${editingConvId}`, {
                method: 'PUT',
                headers: { ...authHeader(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: t }),
            });
            await loadConversations();
            $('#editModal').removeClass('active');
            editingConvId = null;
        } catch { alert('제목 수정 실패'); }
    });

    $(document).on('click', '.delete-btn', async function (e) {
        e.stopPropagation();
        const id = $(this).data('id');
        if (!confirm('이 대화를 삭제하시겠습니까?')) return;
        try {
            await fetch(`/rag/conversations/${id}`, { method: 'DELETE', headers: authHeader() });
            if (currentConvId === id) { currentConvId = null; clearMessages(); }
            await loadConversations();
        } catch { alert('삭제 실패'); }
    });

    /* ──────────────────────────────────────
       9. 공통 유틸
    ────────────────────────────────────── */
    function authHeader() { return { 'Authorization': `Bearer ${localStorage.getItem('access_token')}` }; }
    function escHtml(s) {
        return String(s).replace(/[&<>"']/g, m => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;' }[m]));
    }

    $('#messageInput').on('input', function () {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 150) + 'px';
    });

    /* ── 초기 로드 ── */
    loadStatus();
    loadConversations();
});
