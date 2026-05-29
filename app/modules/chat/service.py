# 챗봇 응답 생성 등 채팅 관련 로직을 처리하는 파일

from anthropic import AsyncAnthropic #, Anthropic
from tavily import TavilyClient
import asyncio
from typing import List, Dict, AsyncGenerator
# local
from app.core.config import settings

# Anthropic, Tavily 클라이언트 초기화
anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
tavily_client = TavilyClient(api_key=settings.TAVILY_API_KEY)

# LLM 검색 툴
TOOLS = [
    {
        "name": "web_search",
        "description": "최신 정보나 사실 확인이 필요할 때 웹 검색",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어"}
            },
            "required": ["query"]
        }
    }
]
# 시스템 프롬프트
SYSTEM_PROMPT = """사무적이고 정중한 어투로 최대한 간략하게 답변하라."""


# 함수 (웹검색 없이 동기식 작동만 필요한 경우)
# async def generate_chat_response(messages: List[Dict[str, str]]) -> AsyncGenerator[str, None]:
#     """
#     Anthropic API를 사용하여 채팅 응답 생성 (스트리밍)
    
#     Args:
#         messages: 대화 히스토리 [{"role": "user", "content": "..."}, ...]
    
#     Yields:
#         str: 응답 텍스트 청크
#     """
#     try:
#         # Anthropic API 호출 (스트리밍)
#         with anthropic_client.messages.stream(
#             model=settings.LLM_MODEL,  # 모델 로드 .env
#             max_tokens=4096,
#             system=SYSTEM_PROMPT,
#             messages=messages
#         ) as stream:
#             for text in stream.text_stream:
#                 yield text
                
#     except Exception as e:
#         yield f"오류가 발생했습니다: {str(e)}"


# 웹 검색 포함 비동기식 함수
async def generate_chat_response(messages: List[Dict[str, str]]) -> AsyncGenerator[str, None]:
    try:
        messages = list(messages)  # 원본 변조 방지

        while True:
            async with anthropic_client.messages.stream(
                model=settings.LLM_MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages
            ) as stream:
                async for text in stream.text_stream:
                    yield text

                final_message = await stream.get_final_message()

            if final_message.stop_reason == "end_turn":
                break

            if final_message.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": final_message.content})

                tool_results = []
                for block in final_message.content:
                    if block.type == "tool_use":
                        # Tavily는 비동기 미지원이므로 스레드로 분리
                        result = await asyncio.to_thread(tavily_client.search, block.input["query"])
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result["results"][:3])
                        })

                messages.append({"role": "user", "content": tool_results})

    except Exception as e:
        yield f"오류가 발생했습니다: {str(e)}"


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