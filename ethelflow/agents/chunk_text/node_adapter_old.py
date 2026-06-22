# Project Ethel
# Node adapter for semantically chunking text
#
# Copyright (C) 2025  Gerd Kortemeyer, ETH Zurich
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
from typing import Callable, Dict, Any, AsyncGenerator
from ethelflow.agents.chunk_text.models import ChunkingRequest, ChunkingResponse
import aiohttp
import os

# could also be an environment variable
# CHUNK_TEXT_URL: str = "http://chunk-text.default.svc:8000/chunk_text"
CHUNK_TEXT_URL: str = "http://chunk-text:8000/chunk_text"


def chunk_text_node(
    input_text_key: str = "text",
    output_key: str = "texts",
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        # 1) Fetch raw text from state
        input_text = state.get(input_text_key)
        if not isinstance(input_text, str):
            raise ValueError(
                f"Expected a string for {input_text_key}, got {type(input_text)}"
            )

        # 2) Build payload and POST to the running chunk_text agent
        request: ChunkingRequest = ChunkingRequest(
            text=input_text, chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                CHUNK_TEXT_URL, json=request.model_dump(), timeout=60
            ) as response:
                # parse the response into a ChunkingResponse object
                if response.status != 200:
                    raise ValueError(
                        f"Chunking service returned status {response.status}"
                    )
                response_data = await response.json()
                data = ChunkingResponse.model_validate(response_data)

        # 3) Extract the “chunks” list (or empty list if missing)
        chunks_list = data.chunks or []
        yield {output_key: chunks_list}

    return node
