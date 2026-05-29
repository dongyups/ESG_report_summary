$(document).ready(function() {
    // 1. 전역 Ajax 에러 핸들러
    // 페이지 내 어떤 Ajax 호출이라도 서버에서 401(Unauthorized)을 반환하면 즉시 로그아웃 시킵니다.
    $(document).ajaxError(function(event, jqXHR, ajaxSettings, thrownError) {
        if (jqXHR.status === 401) {
            console.error("인증 에러 발생: 토큰이 만료되었거나 유효하지 않습니다.");
            alert("세션이 만료되었습니다. 다시 로그인해주세요.");
            logout();
        }
    });

    // 2. 사용자 정보 표시
    const username = localStorage.getItem('full_name') || localStorage.getItem('username') || '사용자';
    $('#userNameDisplay').text(username);

    // 3. 토큰 로드 및 만료 시간 추출
    const token = localStorage.getItem('access_token');
    if (!token) {
        return logout(); // 토큰 없으면 즉시 퇴출
    }

    function getTokenExpiration(token) {
        try {
            // JWT 페이로드 디코딩 (Base64Url & UTF-8 호환)
            const base64Url = token.split('.')[1];
            const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
            const jsonPayload = decodeURIComponent(window.atob(base64).split('').map(function(c) {
                return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
            }).join(''));
            
            const payload = JSON.parse(jsonPayload);
            return payload.exp * 1000; // 초 단위를 밀리초로 변환
        } catch (e) {
            console.error("토큰 파싱 실패:", e);
            return null;
        }
    }

    let tokenEndTime = getTokenExpiration(token);
    if (!tokenEndTime) {
        alert("유효하지 않은 접근입니다.");
        return logout();
    }

    // 4. 실시간 타이머 업데이트
    let timerInterval = setInterval(function() {
        const now = Date.now();
        const timeLeft = tokenEndTime - now;

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

        const formattedTime = 
            String(h).padStart(2, '0') + ':' + 
            String(m).padStart(2, '0') + ':' + 
            String(s).padStart(2, '0');

        $('#tokenTimer').text(formattedTime);
    }, 1000);

    // 5. 로그아웃 기능
    $('#logoutBtn').on('click', function() {
        logout();
    });
    function logout() {
        // 타이머 정지
        if (timerInterval) clearInterval(timerInterval);
        // 로컬 스토리지 일괄 정리
        localStorage.clear();
        // 로그인 화면으로 이동
        window.location.href = '/login'; // page0_login.html
    }
    // 
    // 
    // 챗봇 관련
    let currentConversationId = null;
    let editingConversationId = null;
    let isStreaming = false;

    // 대화 목록 로드
    async function loadConversations() {
        try {
            const response = await fetch('/chat/conversations', {
                headers: {
                    'Authorization': `Bearer ${localStorage.getItem('access_token')}`
                }
            });

            if (!response.ok) {
                if (response.status === 401) {
                    logout();
                    return;
                }
                throw new Error('대화 목록을 불러오는데 실패했습니다');
            }

            const conversations = await response.json();
            renderConversations(conversations);
        } catch (error) {
            console.error('Error loading conversations:', error);
        }
    }

    // 대화 목록 렌더링
    function renderConversations(conversations) {
        const $list = $('#conversationsList');
        $list.empty();

        if (conversations.length === 0) {
            $list.html('<div style="padding: 20px; text-align: center; color: #999; font-size: 13px;">대화가 없습니다</div>');
            return;
        }

        conversations.forEach(conv => {
            const $item = $(`
                <div class="conversation-item ${conv.id === currentConversationId ? 'active' : ''}" data-id="${conv.id}">
                    <div class="conversation-title">${escapeHtml(conv.title)}</div>
                    <div class="conversation-actions">
                        <button class="action-btn edit-btn" data-id="${conv.id}">✏️</button>
                        <button class="action-btn delete-btn" data-id="${conv.id}">🗑️</button>
                    </div>
                </div>
            `);
            $list.append($item);
        });
    }

    // 대화 클릭
    $(document).on('click', '.conversation-item', function(e) {
        if ($(e.target).hasClass('action-btn') || $(e.target).closest('.action-btn').length) {
            return;
        }
        const id = $(this).data('id');
        loadConversation(id);
    });

    // 새 채팅 버튼
    $('#newChatBtn').on('click', async function() {
        try {
            const response = await fetch('/chat/conversations', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${localStorage.getItem('access_token')}`
                },
                body: JSON.stringify({ title: '새 채팅' })
            });

            if (!response.ok) throw new Error('새 채팅 생성 실패');

            const conversation = await response.json();
            currentConversationId = conversation.id;
            
            await loadConversations();
            clearMessages();
        } catch (error) {
            console.error('Error creating conversation:', error);
            alert('새 채팅을 생성하는데 실패했습니다');
        }
    });

    // 대화 로드
    async function loadConversation(conversationId) {
        try {
            const response = await fetch(`/chat/conversations/${conversationId}`, {
                headers: {
                    'Authorization': `Bearer ${localStorage.getItem('access_token')}`
                }
            });

            if (!response.ok) throw new Error('대화 로드 실패');

            const conversation = await response.json();
            currentConversationId = conversationId;
            
            renderMessages(conversation.messages);
            
            $('.conversation-item').removeClass('active');
            $(`.conversation-item[data-id="${conversationId}"]`).addClass('active');
        } catch (error) {
            console.error('Error loading conversation:', error);
            alert('대화를 불러오는데 실패했습니다');
        }
    }

    // 메시지 렌더링
    function renderMessages(messages) {
        const $wrapper = $('#messagesWrapper');
        $wrapper.empty();

        if (messages.length === 0) {
            $wrapper.html(`
                <div class="empty-state">
                    <h2>메시지를 입력하세요</h2>
                    <p>AI와 대화를 시작할 수 있습니다</p>
                </div>
            `);
            return;
        }

        messages.forEach(msg => {
            addMessageToUI(msg.role, msg.content);
        });

        scrollToBottom();
    }

    // 메시지 전송
    $('#sendBtn').on('click', sendMessage);
    $('#messageInput').on('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    async function sendMessage() {
        if (isStreaming) return;

        const content = $('#messageInput').val().trim();
        if (!content) return;

        // 대화가 없으면 새로 생성
        if (!currentConversationId) {
            await $('#newChatBtn').click();
            // 잠시 대기
            await new Promise(resolve => setTimeout(resolve, 500));
        }

        if (!currentConversationId) {
            alert('대화를 생성하는데 실패했습니다');
            return;
        }

        // UI에 사용자 메시지 추가
        addMessageToUI('user', content);
        $('#messageInput').val('');
        
        // 전송 버튼 비활성화
        isStreaming = true;
        $('#sendBtn').prop('disabled', true);

        // AI 응답 영역 추가
        const $assistantMsg = addMessageToUI('assistant', '');
        const $content = $assistantMsg.find('.message-content');
        
        // 타이핑 인디케이터 표시
        $content.html('<div class="typing-indicator"><span></span><span></span><span></span></div>');

        try {
            const response = await fetch(`/chat/conversations/${currentConversationId}/messages`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${localStorage.getItem('access_token')}`
                },
                body: JSON.stringify({ content })
            });

            if (!response.ok) throw new Error('메시지 전송 실패');

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let fullResponse = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value);
                const lines = chunk.split('\n');

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const data = JSON.parse(line.slice(6));
                        
                        if (data.chunk) {
                            fullResponse += data.chunk;
                            $content.text(fullResponse);
                            scrollToBottom();
                        } else if (data.done) {
                            // 완료
                        } else if (data.error) {
                            $content.text('오류: ' + data.error);
                        }
                    }
                }
            }

            // 대화 목록 새로고침
            await loadConversations();

        } catch (error) {
            console.error('Error sending message:', error);
            $content.text('메시지 전송에 실패했습니다: ' + error.message);
        } finally {
            isStreaming = false;
            $('#sendBtn').prop('disabled', false);
        }
    }

    // UI에 메시지 추가
    function addMessageToUI(role, content) {
        const $wrapper = $('#messagesWrapper');
        $('.empty-state').remove();

        const $message = $(`
            <div class="message ${role}">
                <div class="message-role">${role === 'user' ? '사용자' : 'AI 어시스턴트'}</div>
                <div class="message-content">${escapeHtml(content)}</div>
            </div>
        `);

        $wrapper.append($message);
        scrollToBottom();
        return $message;
    }

    function clearMessages() {
        $('#messagesWrapper').html(`
            <div class="empty-state">
                <h2>메시지를 입력하세요</h2>
                <p>AI와 대화를 시작할 수 있습니다</p>
            </div>
        `);
    }

    function scrollToBottom() {
        const $container = $('#chatContainer');
        $container.scrollTop($container[0].scrollHeight);
    }

    // 제목 수정 버튼
    $(document).on('click', '.edit-btn', function(e) {
        e.stopPropagation();
        editingConversationId = $(this).data('id');
        const title = $(this).closest('.conversation-item').find('.conversation-title').text();
        $('#editTitleInput').val(title);
        $('#editModal').addClass('active');
    });

    // 모달 닫기
    $('#cancelEditBtn, #editModal').on('click', function(e) {
        if (e.target === this) {
            $('#editModal').removeClass('active');
            editingConversationId = null;
        }
    });

    // 제목 수정 확인
    $('#confirmEditBtn').on('click', async function() {
        const newTitle = $('#editTitleInput').val().trim();
        if (!newTitle) return;

        try {
            const response = await fetch(`/chat/conversations/${editingConversationId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${localStorage.getItem('access_token')}`
                },
                body: JSON.stringify({ title: newTitle })
            });

            if (!response.ok) throw new Error('제목 수정 실패');

            await loadConversations();
            $('#editModal').removeClass('active');
            editingConversationId = null;
        } catch (error) {
            console.error('Error updating title:', error);
            alert('제목 수정에 실패했습니다');
        }
    });

    // 대화 삭제 버튼
    $(document).on('click', '.delete-btn', async function(e) {
        e.stopPropagation();
        const id = $(this).data('id');
        
        if (!confirm('이 대화를 삭제하시겠습니까?')) return;

        try {
            const response = await fetch(`/chat/conversations/${id}`, {
                method: 'DELETE',
                headers: {
                    'Authorization': `Bearer ${localStorage.getItem('access_token')}`
                }
            });

            if (!response.ok) throw new Error('대화 삭제 실패');

            if (currentConversationId === id) {
                currentConversationId = null;
                clearMessages();
            }

            await loadConversations();
        } catch (error) {
            console.error('Error deleting conversation:', error);
            alert('대화 삭제에 실패했습니다');
        }
    });

    // HTML 이스케이프
    function escapeHtml(text) {
        const map = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        };
        return text.replace(/[&<>"']/g, m => map[m]);
    }

    // Textarea 자동 높이 조절
    $('#messageInput').on('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 150) + 'px';
    });

    // 초기 로드
    loadConversations();
});