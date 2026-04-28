# 챗봇 응답 생성 등 채팅 관련 로직을 처리하는 파일

import anthropic
from typing import List, Dict, AsyncGenerator
# local
from app.core.config import settings

# Anthropic 클라이언트 초기화
client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

# 시스템 프롬프트
SYSTEM_PROMPT = """사무적이고 정중한 어투로 최대한 간략하게 답변하라."""


async def generate_chat_response(messages: List[Dict[str, str]]) -> AsyncGenerator[str, None]:
    """
    Anthropic API를 사용하여 채팅 응답 생성 (스트리밍)
    
    Args:
        messages: 대화 히스토리 [{"role": "user", "content": "..."}, ...]
    
    Yields:
        str: 응답 텍스트 청크
    """
    try:
        # Anthropic API 호출 (스트리밍)
        with client.messages.stream(
            model=settings.LLM_MODEL,  # 모델 로드 .env
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=messages
        ) as stream:
            for text in stream.text_stream:
                yield text
                
    except Exception as e:
        yield f"오류가 발생했습니다: {str(e)}"


async def generate_chat_response_complete(messages: List[Dict[str, str]]) -> str:
    """
    Anthropic API를 사용하여 채팅 응답 생성 (완전한 응답)
    
    Args:
        messages: 대화 히스토리 [{"role": "user", "content": "..."}, ...]
    
    Returns:
        str: 완전한 응답 텍스트
    """
    try:
        # Anthropic API 호출
        response = client.messages.create(
            model=settings.LLM_MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=messages
        )
        
        # 응답 텍스트 추출
        return response.content[0].text
        
    except Exception as e:
        return f"오류가 발생했습니다: {str(e)}"


def format_messages_for_api(db_messages: List) -> List[Dict[str, str]]:
    """
    데이터베이스의 메시지를 Anthropic API 형식으로 변환
    
    Args:
        db_messages: 데이터베이스 Message 객체 리스트
    
    Returns:
        List[Dict]: Anthropic API 형식의 메시지 리스트
    """
    formatted = []
    for msg in db_messages:
        formatted.append({
            "role": msg.role,
            "content": msg.content
        })
    return formatted