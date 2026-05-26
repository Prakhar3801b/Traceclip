import ast
import re
from typing import List
from traceclip.detector import CodeAnalyzer, Issue, scan_comments_for_placeholders

def fix_file(source_code: str, file_path: str) -> str:
    # 1. Detect issues using the same engine
    try:
        analyzer = CodeAnalyzer(file_path, source_code)
        analyzer.analyze()
        issues = analyzer.issues
        # Exclude syntax errors and wildcard imports from being fixed automatically
        issues = [i for i in issues if i.issue_type not in ("syntax_error", "wildcard_import")]
    except Exception:
        # If AST parsing fails, return source_code unmodified
        return source_code

    if not issues:
        return source_code

    # 2. Sort issues in descending order of line number to process bottom-up
    # This prevents shifting lines of unprocessed issues above the current edit.
    issues.sort(key=lambda x: (x.line, x.col), reverse=True)

    lines = source_code.splitlines()
    modified_lines = set()
    needs_os_import = False

    for issue in issues:
        # Avoid double-modifying lines (e.g. if a function is both a stub and dead)
        line_range = range(issue.line, issue.end_line + 1)
        if any(l in modified_lines for l in line_range):
            continue

        # Mark range as modified
        for l in line_range:
            modified_lines.add(l)

        if issue.issue_type == "unused_import":
            # Find the import node in the analyzer
            # Let's search the imports dict for the one matching this line
            matching_import_info = None
            bound_name_to_remove = None
            for name, info in analyzer.imports.items():
                import_line, import_col, node, alias = info
                if import_line == issue.line:
                    # Match by line number and column
                    matching_import_info = info
                    bound_name_to_remove = name
                    break

            if matching_import_info:
                _, _, node, alias_node = matching_import_info
                start_idx = node.lineno - 1
                end_idx = getattr(node, "end_lineno", node.lineno) - 1

                # Check if this import statement has multiple imported names
                if len(node.names) > 1:
                    # Reconstruct separate imports for all names that are NOT unused
                    used_aliases = []
                    for a in node.names:
                        # Determine bound name for this alias
                        b_name = a.asname if a.asname else (a.name if isinstance(node, ast.ImportFrom) else a.name.split('.')[0])
                        # A name is used if it's not the one we are removing
                        if b_name != bound_name_to_remove and b_name in analyzer.name_loads:
                            used_aliases.append(a)

                    # Comment out the old import statement
                    for idx in range(start_idx, end_idx + 1):
                        lines[idx] = f"# vibecheck-removed: {lines[idx]}"

                    if used_aliases:
                        # Build the new import line
                        if isinstance(node, ast.Import):
                            new_import_parts = [f"{a.name} as {a.asname}" if a.asname else a.name for a in used_aliases]
                            new_import = "import " + ", ".join(new_import_parts)
                        else:
                            new_import_parts = [f"{a.name} as {a.asname}" if a.asname else a.name for a in used_aliases]
                            new_import = f"from {node.module} import " + ", ".join(new_import_parts)
                        
                        # Append the new import statement right below the commented one
                        lines[end_idx] = lines[end_idx] + "\n" + new_import
                else:
                    # Single name import: comment out the whole thing
                    for idx in range(start_idx, end_idx + 1):
                        lines[idx] = f"# vibecheck-removed: {lines[idx]}"

        elif issue.issue_type in ("dead_function", "stub_function"):
            start_idx = issue.line - 1
            end_idx = issue.end_line - 1
            prefix = "# vibecheck-removed: " if issue.issue_type == "dead_function" else "# vibecheck-stub-removed: "
            for idx in range(start_idx, end_idx + 1):
                lines[idx] = f"{prefix}{lines[idx]}"

        elif issue.issue_type == "placeholder_var":
            # For placeholder variables (API_KEY = "your_key"), replace with os.getenv
            # Check if this is a comment placeholder or variable placeholder
            # If it's a comment, we don't change the code (just keep it as is, or we can remove it. Let's just comment it out further or leave it)
            if "comment:" in issue.message.lower():
                # Comment placeholder, we can prefix the comment line with a warning or just leave it
                continue

            # Variable placeholder: e.g. API_KEY = "your_key"
            start_idx = issue.line - 1
            end_idx = issue.end_line - 1
            
            # Since AST is valid, we can parse this assignment line
            # Let's extract the target and value
            line_str = lines[start_idx]
            if "=" in line_str:
                parts = line_str.split("=", 1)
                left_side = parts[0].rstrip()
                # Find the target name
                # Let's guess the target variable name: we can get it from the issue message or parse it
                # E.g. "Placeholder value 'your_key' assigned to variable/attribute 'API_KEY'."
                # We can extract the target name from the message or left_side
                target_name = left_side.strip().split()[-1] # Simple guess
                # Clean up characters like colon for typed annotations
                if ":" in target_name:
                    target_name = target_name.split(":")[0].strip()
                
                # Extract the original string literal value
                # We know the issue.message contains "Placeholder value 'val' assigned to..."
                # Let's extract 'val' from the message using regex
                val_match = re.search(r"Placeholder value '(.*)' assigned to", issue.message)
                val_str = val_match.group(1) if val_match else "your_key_here"

                # Replace with os.getenv("TARGET_NAME", "default_val")
                new_assignment = f"{left_side} = os.getenv({target_name!r}, {val_str!r})"
                lines[start_idx] = f"# vibecheck-placeholder-fixed:\n# {line_str}\n{new_assignment}"
                needs_os_import = True

    # 3. Add import os if needed and not already imported and kept
    if needs_os_import and ("os" not in analyzer.imports or "os" not in analyzer.name_loads):
        lines.insert(0, "import os")

    return "\n".join(lines) + "\n"
