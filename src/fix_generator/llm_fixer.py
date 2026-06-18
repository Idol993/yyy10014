import os
import re
import json
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False
    def retry(*args, **kwargs):
        def decorator(func):
            return func
        return decorator
    def stop_after_attempt(*args, **kwargs):
        return None
    def wait_exponential(*args, **kwargs):
        return None
    def retry_if_exception_type(*args, **kwargs):
        return None

from src.log_parser import BuildError
from src.ast_diff import CodeChange
from src.rag import RetrievalResult


@dataclass
class FixPatch:
    file_path: str
    original_code: str
    fixed_code: str
    line_start: int
    line_end: int
    explanation: str = ""
    confidence: float = 0.0
    
    @property
    def is_valid(self) -> bool:
        return (self.file_path and 
                self.original_code and 
                self.fixed_code and 
                self.original_code != self.fixed_code)


@dataclass
class FixResult:
    patches: List[FixPatch] = field(default_factory=list)
    summary: str = ""
    approach: str = ""
    success: bool = False
    error_message: str = ""
    
    @property
    def has_patches(self) -> bool:
        return len(self.patches) > 0
    
    @property
    def valid_patches(self) -> List[FixPatch]:
        return [p for p in self.patches if p.is_valid]


class LLMFixer:
    def __init__(self, openai_api_key: str, model: str = "gpt-4-turbo-preview",
                 temperature: float = 0.1):
        self.openai_api_key = openai_api_key
        self.model = model
        self.temperature = temperature
        self._client = None
    
    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.openai_api_key)
        return self._client
    
    def generate_fix(self, error: BuildError, code_changes: List[CodeChange],
                     context: RetrievalResult, file_content: str,
                     max_attempts: int = 1) -> FixResult:
        result = FixResult()
        
        try:
            prompt = self._build_fix_prompt(error, code_changes, context, file_content)
            
            response = self._call_llm(prompt)
            patches = self._parse_fix_response(response, file_content, error.file_path)
            
            result.patches = patches
            result.summary = self._extract_summary(response)
            result.approach = self._extract_approach(response)
            result.success = len(patches) > 0
            
        except Exception as e:
            result.success = False
            result.error_message = str(e)
        
        return result
    
    def _build_fix_prompt(self, error: BuildError, code_changes: List[CodeChange],
                          context: RetrievalResult, file_content: str) -> str:
        error_info = f"""
## Build Error Information

Error Type: {error.error_type}
Error Message: {error.message}
File: {error.file_path or 'unknown'}
Line: {error.line_number or 'unknown'}
Column: {error.column_number or 'unknown'}
Error Code: {error.error_code or 'N/A'}
"""
        
        if error.full_context:
            error_info += f"\nError Context:\n```\n{error.full_context}\n```\n"
        
        changes_info = ""
        if code_changes:
            changes_info = "\n## Recent Code Changes (from last commit)\n\n"
            for i, change in enumerate(code_changes, 1):
                changes_info += f"""
### Change {i} ({change.change_type.value})
- Line: {change.line_number}-{change.end_line}
- Function: {change.function_name or 'unknown'}
- Class: {change.class_name or 'unknown'}
- AST Node: {change.ast_node_type or 'unknown'}

Old code:
```
{change.old_code or 'N/A'}
```

New code:
```
{change.new_code or 'N/A'}
```
"""
        
        context_info = ""
        if context.has_results:
            context_info = "\n## Relevant Documentation and Context\n\n"
            context_info += context.get_formatted_context()
        
        file_info = ""
        if file_content:
            lines = file_content.splitlines()
            file_info = f"\n## Full File Content ({error.file_path})\n\n```\n"
            
            if error.line_number:
                start = max(0, error.line_number - 50)
                end = min(len(lines), error.line_number + 50)
                for i in range(start, end):
                    marker = ">>> " if i + 1 == error.line_number else "    "
                    file_info += f"{marker}{i+1:4d}: {lines[i]}\n"
            else:
                file_info += file_content
            
            file_info += "\n```\n"
        
        prompt = f"""You are an expert software engineer tasked with fixing a build error.

{error_info}
{changes_info}
{context_info}
{file_info}

## Task

Analyze the build error and recent code changes to determine the root cause. Then generate a fix.

## Instructions

1. First, identify the root cause of the error
2. Consider the recent code changes - they are likely the cause
3. Use the documentation and context to inform your fix
4. Generate a complete, working fix

## Output Format

Please provide your response in the following JSON format:

```json
{{
  "root_cause_analysis": "Detailed explanation of what caused the error",
  "fix_approach": "Description of your approach to fixing the issue",
  "patches": [
    {{
      "file_path": "path/to/file.ext",
      "line_start": 10,
      "line_end": 15,
      "original_code": "the original code block",
      "fixed_code": "the fixed code block",
      "explanation": "Why this change fixes the issue"
    }}
  ],
  "confidence": 0.9,
  "risk_assessment": "low|medium|high",
  "risk_details": "Explanation of any potential risks or side effects"
}}
```

Important:
- The original_code must exactly match the code in the file
- The fixed_code must be the complete replacement code
- Include only the specific lines that need to change
- Make minimal, targeted changes
- Ensure the fix doesn't break anything else
- Follow the existing code style and conventions
"""
        
        return prompt
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((Exception,)),
    )
    def _call_llm(self, prompt: str) -> str:
        client = self._get_client()
        
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a senior software engineer and build expert. "
                              "Your job is to fix build errors with minimal, safe changes."
                },
                {"role": "user", "content": prompt},
            ],
            temperature=self.temperature,
            max_tokens=4096,
        )
        
        return response.choices[0].message.content
    
    def _parse_fix_response(self, response: str, file_content: str, 
                            default_file: Optional[str]) -> List[FixPatch]:
        patches = []
        
        json_str = self._extract_json(response)
        
        if json_str:
            try:
                data = json.loads(json_str)
                
                patch_data_list = data.get("patches", [])
                for patch_data in patch_data_list:
                    patch = FixPatch(
                        file_path=patch_data.get("file_path", default_file or ""),
                        original_code=patch_data.get("original_code", ""),
                        fixed_code=patch_data.get("fixed_code", ""),
                        line_start=patch_data.get("line_start", 0),
                        line_end=patch_data.get("line_end", 0),
                        explanation=patch_data.get("explanation", ""),
                        confidence=data.get("confidence", 0.0),
                    )
                    patches.append(patch)
                    
            except json.JSONDecodeError:
                pass
        
        if not patches:
            patches = self._parse_code_blocks(response, file_content, default_file)
        
        return patches
    
    def _extract_json(self, text: str) -> Optional[str]:
        pattern = r'```json\s*(.*?)\s*```'
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1)
        
        brace_count = 0
        start_idx = -1
        
        for i, char in enumerate(text):
            if char == '{':
                if brace_count == 0:
                    start_idx = i
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0 and start_idx >= 0:
                    return text[start_idx:i+1]
        
        return None
    
    def _parse_code_blocks(self, response: str, file_content: str,
                           default_file: Optional[str]) -> List[FixPatch]:
        patches = []
        
        pattern = r'```(?:\w+)?\s*(.*?)\s*```'
        blocks = re.findall(pattern, response, re.DOTALL)
        
        return patches
    
    def _extract_summary(self, response: str) -> str:
        json_str = self._extract_json(response)
        if json_str:
            try:
                data = json.loads(json_str)
                return data.get("root_cause_analysis", "")
            except json.JSONDecodeError:
                pass
        
        lines = response.strip().split('\n')
        return lines[0] if lines else ""
    
    def _extract_approach(self, response: str) -> str:
        json_str = self._extract_json(response)
        if json_str:
            try:
                data = json.loads(json_str)
                return data.get("fix_approach", "")
            except json.JSONDecodeError:
                pass
        
        return ""
    
    def apply_patch(self, patch: FixPatch, file_content: str) -> str:
        original = patch.original_code.strip()
        fixed = patch.fixed_code.strip()
        content = file_content
        
        if original and fixed and original != fixed:
            original_lines = original.split('\n')
            content_lines = content.split('\n')
            
            for i in range(len(content_lines) - len(original_lines) + 1):
                match = True
                for j in range(len(original_lines)):
                    if content_lines[i + j].strip() != original_lines[j].strip():
                        match = False
                        break
                
                if match:
                    new_lines = (content_lines[:i] + 
                                fixed.split('\n') + 
                                content_lines[i + len(original_lines):])
                    return '\n'.join(new_lines)
        
        if patch.line_start > 0 and patch.line_end > 0 and fixed:
            lines = content.split('\n')
            fixed_lines = fixed.split('\n')
            new_lines = (lines[:patch.line_start - 1] + 
                        fixed_lines + 
                        lines[patch.line_end:])
            return '\n'.join(new_lines)
        
        return content
    
    def refine_fix(self, error: BuildError, patch: FixPatch, 
                   build_result: str, context: RetrievalResult) -> FixPatch:
        prompt = f"""
The previous fix attempt did not fully resolve the issue. Please refine the fix.

## Original Error
Type: {error.error_type}
Message: {error.message}
File: {error.file_path}
Line: {error.line_number}

## Previous Fix Attempt
File: {patch.file_path}
Original code:
```
{patch.original_code}
```

Fixed code:
```
{patch.fixed_code}
```

Explanation: {patch.explanation}

## New Build Result
```
{build_result}
```

## Context
{context.get_formatted_context() if context.has_results else 'No additional context'}

## Task

Analyze why the previous fix didn't work and generate an improved fix.

Output in the same JSON format as before.
"""
        
        response = self._call_llm(prompt)
        
        new_patches = self._parse_fix_response(response, "", patch.file_path)
        
        if new_patches:
            return new_patches[0]
        
        return patch
    
    def generate_multiple_approaches(self, error: BuildError, 
                                      code_changes: List[CodeChange],
                                      context: RetrievalResult,
                                      file_content: str,
                                      num_approaches: int = 3) -> List[FixResult]:
        results = []
        
        for i in range(num_approaches):
            prompt = self._build_fix_prompt(error, code_changes, context, file_content)
            prompt += f"\n\nThis is approach number {i+1} of {num_approaches}. "
            prompt += "Try a different approach than you would normally use."
            
            try:
                response = self._call_llm(prompt)
                patches = self._parse_fix_response(response, file_content, error.file_path)
                
                result = FixResult(
                    patches=patches,
                    summary=self._extract_summary(response),
                    approach=self._extract_approach(response),
                    success=len(patches) > 0,
                )
                results.append(result)
            except Exception:
                pass
        
        return results
