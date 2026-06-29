$(document).ready(function() {
    // Form submission
    $('#loginForm').on('submit', function(e) {
        e.preventDefault();
        
        const username = $('#username').val().trim();
        const password = $('#password').val();
        
        if (!username || !password) {
            showError('아이디와 비밀번호를 모두 입력해주세요.');
            return;
        }
        
        login(username, password);
    });
    
    // Login function
    function login(username, password) {
        const $button = $('#loginButton');
        const originalText = $button.text();
        
        // Disable button and show loading
        $button.prop('disabled', true);
        $button.html('<span class="spinner"></span>로그인 중...');
        
        // Hide error message
        $('#errorMessage').hide();
        
        $.ajax({
            url: '/auth/login',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                username: username,
                password: password
            }),
            success: function(response) {
                // Store tokens and user info in localStorage
                localStorage.setItem('access_token', response.access_token);
                localStorage.setItem('refresh_token', response.refresh_token);
                localStorage.setItem('user_id', response.user.id);
                localStorage.setItem('username', response.user.username);
                localStorage.setItem('full_name', response.user.full_name || response.user.username);
                
                // Redirect to page1 (chatbot) / page2 (rawdb)
                window.location.href = '/rawdb';
            },
            error: function(xhr) {
                // Re-enable button
                $button.prop('disabled', false);
                $button.text(originalText);
                
                // Show error message
                let errorMsg = '로그인에 실패했습니다.';
                if (xhr.responseJSON && xhr.responseJSON.detail) {
                    errorMsg = xhr.responseJSON.detail;
                }
                showError(errorMsg);
            }
        });
    }
    
    // Show error message
    function showError(message) {
        $('#errorMessage').text(message).fadeIn();
    }
    
    // Enter key handling
    $('#username, #password').on('keypress', function(e) {
        if (e.which === 13) {
            $('#loginForm').submit();
        }
    });
});