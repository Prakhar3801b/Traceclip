import unittest
import time
from traceclip.detector import detect_issues
from traceclip.fixer import fix_file

class TestPerformance(unittest.TestCase):
    def test_large_file_performance(self):
        lines = []
        
        # 1. Add 100 unused imports
        for i in range(100):
            lines.append(f"import unused_module_{i}")
            
        # Add used imports
        lines.append("import os")
        lines.append("from collections import defaultdict")
        
        # 2. Add 200 placeholder variables (100 API keys, 100 DB URLs)
        for i in range(100):
            lines.append(f"API_KEY_{i} = 'your_api_key_here_{i}'")
            lines.append(f"DB_URL_{i} = 'insert_db_url_here'")
            
        # 3. Add 100 stub functions (which will also be dead)
        for i in range(100):
            lines.append(f"def stub_function_{i}():")
            lines.append("    pass")
            
        # 4. Add 250 active functions (chained calls)
        for i in range(250):
            lines.append(f"def active_function_{i}(x):")
            if i == 0:
                lines.append("    return x + 1")
            else:
                lines.append(f"    return active_function_{i-1}(x) + 1")
                
        # 5. Add 250 dead functions
        for i in range(250):
            lines.append(f"def dead_function_{i}(x):")
            lines.append("    return x * 2")
            
        # 6. Entrypoint using active functions
        lines.append("if __name__ == '__main__':")
        lines.append("    d = defaultdict(int)")
        lines.append("    print(active_function_249(10))")
        
        large_code = "\n".join(lines)
        line_count = len(lines)
        
        # Measure detection speed
        start_detect = time.perf_counter()
        issues = detect_issues(large_code, "large_file.py")
        detect_duration = time.perf_counter() - start_detect
        
        # Measure fixing speed
        start_fix = time.perf_counter()
        fixed_code = fix_file(large_code, "large_file.py")
        fix_duration = time.perf_counter() - start_fix
        
        # Print stats
        print(f"\n[Stress Test] Generated codebase: {line_count} lines.")
        print(f"  - Detected {len(issues)} issues in {detect_duration:.4f} seconds.")
        print(f"  - Fixed codebase in {fix_duration:.4f} seconds.")
        
        # Performance assertions (sub-second threshold)
        self.assertLess(detect_duration, 1.0, f"Detection took too long: {detect_duration:.4f}s")
        self.assertLess(fix_duration, 1.5, f"Fixing took too long: {fix_duration:.4f}s")
        
        # Correctness checks: we expect:
        # - 100 unused imports
        # - 200 placeholder variables
        # - 100 stub functions
        # - 250 dead functions
        # - 100 stub functions (which are also dead functions)
        # Total issues should be at least 750
        self.assertGreaterEqual(len(issues), 750)
        
        # Syntax check: verify the fixed codebase compiles successfully as valid Python
        try:
            compile(fixed_code, "large_file.py", "exec")
        except SyntaxError as e:
            self.fail(f"Autofixer produced syntactically invalid Python code: {e}")

if __name__ == "__main__":
    unittest.main()
