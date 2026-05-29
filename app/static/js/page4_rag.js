$(document).ready(function() {
    // 전역 Ajax 에러 핸들러
    // 페이지 내 어떤 Ajax 호출이라도 서버에서 401(Unauthorized)을 반환하면 즉시 로그아웃
    $(document).ajaxError(function(event, jqXHR, ajaxSettings, thrownError) {
        if (jqXHR.status === 401) {
            console.error("인증 에러 발생: 토큰이 만료되었거나 유효하지 않습니다.");
            alert("세션이 만료되었습니다. 다시 로그인해주세요.");
            logout();
        }
    });

    // 사용자 정보 표시
    const username = localStorage.getItem('full_name') || localStorage.getItem('username') || '사용자';
    $('#userNameDisplay').text(username);

    // 토큰 로드 및 만료 시간 추출
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

    // 실시간 타이머 업데이트
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

    // 로그아웃 기능
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
});