#!/usr/bin/env python3
"""
文本分块工具 - 用于将长文本拆分为LLM可处理的大小块

功能：
- 按字符数分块（默认8000字符）
- 保留重叠行保持上下文
- 智能切分（不切断句子/表格/列表）
"""

import re
from typing import List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class TextChunk:
    """文本块"""
    chunk_id: int
    content: str
    start_line: int
    end_line: int
    start_char: int
    end_char: int
    is_continuation: bool = False


class TextChunker:
    """文本分块器"""

    def __init__(
        self,
        max_chunk_size: int = 8000,
        min_chunk_size: int = 1000,
        overlap_lines: int = 3,
        overlap_chars: int = 200
    ):
        """
        初始化分块器

        Args:
            max_chunk_size: 最大块大小（字符数）
            min_chunk_size: 最小块大小（字符数）
            overlap_lines: 重叠行数
            overlap_chars: 重叠字符数（按字符重叠而非行重叠时）
        """
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.overlap_lines = overlap_lines
        self.overlap_chars = overlap_chars

    def chunk_by_lines(
        self,
        text: str,
        lines: List[str] = None
    ) -> List[TextChunk]:
        """
        按行分块，保留上下文重叠

        Args:
            text: 完整文本
            lines: 可选的预分割行列表

        Returns:
            TextChunk列表
        """
        if lines is None:
            lines = text.split('\n')

        chunks = []
        chunk_id = 0
        current_pos = 0
        current_lines = []

        for i, line in enumerate(lines):
            current_lines.append(line)
            current_text = '\n'.join(current_lines)

            # 如果超过最大块大小
            if len(current_text) > self.max_chunk_size:
                # 保存当前块（减去最后一行）
                if len(current_lines) > 1:
                    current_text = '\n'.join(current_lines[:-1])
                    chunks.append(TextChunk(
                        chunk_id=chunk_id,
                        content=current_text,
                        start_line=current_pos,
                        end_line=i - 1,
                        start_char=sum(len(l) + 1 for l in lines[:current_pos]),
                        end_char=sum(len(l) + 1 for l in lines[:i - 1]),
                        is_continuation=False
                    ))
                    chunk_id += 1

                # 开始新块，保留重叠行
                overlap_start = max(0, i - self.overlap_lines)
                current_lines = lines[overlap_start:i]
                current_pos = overlap_start
                current_text = '\n'.join(current_lines)

                # 如果单行就超过最大块
                if len(line) > self.max_chunk_size:
                    # 递归按字符分块
                    sub_chunks = self._chunk_single_line(line, chunk_id, i)
                    chunks.extend(sub_chunks)
                    chunk_id = sub_chunks[-1].chunk_id + 1
                    current_lines = []
                    current_pos = i + 1

        # 处理最后一块
        if current_lines:
            current_text = '\n'.join(current_lines)
            if len(current_text) >= self.min_chunk_size or not chunks:
                chunks.append(TextChunk(
                    chunk_id=chunk_id,
                    content=current_text,
                    start_line=current_pos,
                    end_line=len(lines) - 1,
                    start_char=sum(len(l) + 1 for l in lines[:current_pos]),
                    end_char=sum(len(l) + 1 for l in lines) - 1,
                    is_continuation=False
                ))

        return chunks

    def _chunk_single_line(
        self,
        line: str,
        start_chunk_id: int,
        line_num: int
    ) -> List[TextChunk]:
        """处理单行超长文本"""
        chunks = []
        start_char = 0

        while start_char < len(line):
            end_char = min(start_char + self.max_chunk_size, len(line))
            if start_char > 0:
                end_char = min(end_char + 200, len(line))  # 保留一些重叠

            content = line[start_char:end_char]
            is_continuation = start_char > 0

            chunks.append(TextChunk(
                chunk_id=start_chunk_id + len(chunks),
                content=content,
                start_line=line_num,
                end_line=line_num,
                start_char=start_char,
                end_char=end_char,
                is_continuation=is_continuation
            ))

            start_char = end_char - 200 if end_char < len(line) else end_char

        return chunks

    def chunk_by_semantic(
        self,
        text: str,
        separators: List[str] = None
    ) -> List[TextChunk]:
        """
        按语义分块（尝试在段落/表格/列表边界切分）

        Args:
            text: 完整文本
            separators: 分隔符列表（按优先级）

        Returns:
            TextChunk列表
        """
        if separators is None:
            separators = [
                '\n\n',      # 段落分隔
                '\n',        # 行分隔
                '。\n',      # 句子+换行
                '；\n',      # 分句+换行
                '. ',        # 英文句子
            ]

        chunks = []
        current_chunk = []
        current_size = 0
        chunk_id = 0

        # 简单的段落分割
        paragraphs = re.split(r'\n\n+', text)

        for para in paragraphs:
            para_size = len(para)

            # 如果单个段落就超过最大块
            if para_size > self.max_chunk_size:
                # 先保存当前块
                if current_chunk:
                    chunks.append(TextChunk(
                        chunk_id=chunk_id,
                        content='\n\n'.join(current_chunk),
                        start_line=0,
                        end_line=0,
                        start_char=0,
                        end_char=current_size,
                        is_continuation=False
                    ))
                    chunk_id += 1
                    current_chunk = []
                    current_size = 0

                # 递归分块这个大段落
                sub_chunks = self.chunk_by_lines(para)
                chunks.extend([
                    TextChunk(
                        chunk_id=chunk_id + i,
                        content=sc.content,
                        start_line=sc.start_line,
                        end_line=sc.end_line,
                        start_char=sc.start_char,
                        end_char=sc.end_char,
                        is_continuation=sc.is_continuation
                    )
                    for i, sc in enumerate(sub_chunks)
                ])
                chunk_id += len(sub_chunks)
                continue

            # 如果加上当前段落会超限
            if current_size + para_size > self.max_chunk_size and current_chunk:
                chunks.append(TextChunk(
                    chunk_id=chunk_id,
                    content='\n\n'.join(current_chunk),
                    start_line=0,
                    end_line=0,
                    start_char=0,
                    end_char=current_size,
                    is_continuation=False
                ))
                chunk_id += 1

                # 保留重叠内容
                overlap_text = '\n\n'.join(current_chunk)[-self.overlap_chars:]
                current_chunk = [overlap_text]
                current_size = len(overlap_text)

            current_chunk.append(para)
            current_size += para_size

        # 处理最后一块
        if current_chunk:
            chunks.append(TextChunk(
                chunk_id=chunk_id,
                content='\n\n'.join(current_chunk),
                start_line=0,
                end_line=0,
                start_char=0,
                end_char=current_size,
                is_continuation=False
            ))

        return chunks

    def get_context_header(
        self,
        previous_chunk: Optional[TextChunk],
        overlap_lines: int = 3
    ) -> str:
        """
        获取上下文头部（用于多块审查时保持连续性）

        Args:
            previous_chunk: 前一个块
            overlap_lines: 保留前一个块的最后几行

        Returns:
            上下文头部文本
        """
        if previous_chunk is None:
            return ""

        lines = previous_chunk.content.split('\n')
        context = '\n'.join(lines[-overlap_lines:])
        return f"[接上文内容]\n{context}\n[继续审查]\n"


def create_chunks(
    text: str,
    max_size: int = 8000,
    overlap_lines: int = 3,
    method: str = "lines"
) -> List[TextChunk]:
    """
    便捷函数：创建文本块

    Args:
        text: 完整文本
        max_size: 最大块大小
        overlap_lines: 重叠行数
        method: 分块方法 ("lines" 或 "semantic")

    Returns:
        TextChunk列表
    """
    chunker = TextChunker(
        max_chunk_size=max_size,
        overlap_lines=overlap_lines
    )

    if method == "semantic":
        return chunker.chunk_by_semantic(text)
    else:
        return chunker.chunk_by_lines(text)


if __name__ == "__main__":
    # 测试
    sample_text = """
    这是一个测试文本。

    第二段内容，包含一些测试数据。

    第三段内容。

    第四段内容。

    第五段内容。
    """

    chunks = create_chunks(sample_text, max_size=50, method="lines")
    print(f"生成了 {len(chunks)} 个块")
    for i, chunk in enumerate(chunks):
        print(f"\n--- 块 {chunk.chunk_id} ({len(chunk.content)} 字符) ---")
        print(chunk.content[:100] + "..." if len(chunk.content) > 100 else chunk.content)
