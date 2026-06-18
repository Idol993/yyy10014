import os
import tempfile
import pytest

from src.fix_generator import LLMFixer, FixPatch, FixResult


class TestFixPatch:
    def test_valid_patch(self):
        patch = FixPatch(
            file_path="test.py",
            original_code="print('hello')",
            fixed_code="print('world')",
            line_start=1,
            line_end=1,
        )
        
        assert patch.is_valid
    
    def test_invalid_patch_same_code(self):
        patch = FixPatch(
            file_path="test.py",
            original_code="print('hello')",
            fixed_code="print('hello')",
            line_start=1,
            line_end=1,
        )
        
        assert not patch.is_valid
    
    def test_invalid_patch_empty_file(self):
        patch = FixPatch(
            file_path="",
            original_code="old",
            fixed_code="new",
            line_start=1,
            line_end=1,
        )
        
        assert not patch.is_valid
    
    def test_invalid_patch_empty_code(self):
        patch = FixPatch(
            file_path="test.py",
            original_code="",
            fixed_code="",
            line_start=1,
            line_end=1,
        )
        
        assert not patch.is_valid


class TestFixResult:
    def test_no_patches(self):
        result = FixResult()
        
        assert not result.has_patches
        assert len(result.valid_patches) == 0
    
    def test_with_patches(self):
        patches = [
            FixPatch(
                file_path="a.py",
                original_code="old1",
                fixed_code="new1",
                line_start=1,
                line_end=1,
            ),
            FixPatch(
                file_path="b.py",
                original_code="old2",
                fixed_code="old2",
                line_start=1,
                line_end=1,
            ),
        ]
        
        result = FixResult(patches=patches)
        
        assert result.has_patches
        assert len(result.valid_patches) == 1


class TestLLMFixer:
    def test_apply_patch_by_content(self):
        fixer = LLMFixer(openai_api_key="test")
        
        original_content = """def hello():
    print('old message')
    return True
"""
        
        patch = FixPatch(
            file_path="test.py",
            original_code="    print('old message')",
            fixed_code="    print('new message')",
            line_start=2,
            line_end=2,
        )
        
        result = fixer.apply_patch(patch, original_content)
        
        assert "new message" in result
        assert "old message" not in result
    
    def test_apply_patch_by_line_numbers(self):
        fixer = LLMFixer(openai_api_key="test")
        
        original_content = """line1
line2
line3
line4
line5
"""
        
        patch = FixPatch(
            file_path="test.py",
            original_code="",
            fixed_code="new_line2\nnew_line3",
            line_start=2,
            line_end=3,
        )
        
        result = fixer.apply_patch(patch, original_content)
        lines = result.strip().split('\n')
        
        assert len(lines) == 5
        assert lines[0] == "line1"
        assert lines[1] == "new_line2"
        assert lines[2] == "new_line3"
        assert lines[3] == "line4"
        assert lines[4] == "line5"
    
    def test_apply_patch_no_change(self):
        fixer = LLMFixer(openai_api_key="test")
        
        original_content = "unchanged\n"
        patch = FixPatch(
            file_path="",
            original_code="",
            fixed_code="",
            line_start=0,
            line_end=0,
        )
        
        result = fixer.apply_patch(patch, original_content)
        
        assert result == original_content
    
    def test_extract_json_from_code_block(self):
        fixer = LLMFixer(openai_api_key="test")
        
        text = '''
Here is the result:

```json
{
  "key": "value",
  "number": 42
}
```

That's it.
'''
        
        json_str = fixer._extract_json(text)
        
        assert json_str is not None
        assert '"key": "value"' in json_str
    
    def test_extract_json_no_code_block(self):
        fixer = LLMFixer(openai_api_key="test")
        
        text = '''
Here is some text without a JSON block.
Just plain text.
'''
        
        json_str = fixer._extract_json(text)
        
        assert json_str is None
