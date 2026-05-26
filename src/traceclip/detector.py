import ast
import re
from dataclasses import dataclass
from typing import List, Set, Dict, Any, Tuple

# Severity weights for scoring
WEIGHT_UNUSED_IMPORT = 5
WEIGHT_WILDCARD_IMPORT = 5
WEIGHT_DEAD_FUNCTION = 10
WEIGHT_STUB_FUNCTION = 15
WEIGHT_PLACEHOLDER_VAR = 20

@dataclass
class Issue:
    file_path: str
    line: int
    col: int
    issue_type: str
    severity: str
    message: str
    weight: int
    end_line: int = 0
    context: str = ""

class CodeAnalyzer(ast.NodeVisitor):
    def __init__(self, file_path: str, source_code: str):
        self.file_path = file_path
        self.source_code = source_code
        self.source_lines = source_code.splitlines()
        self.issues: List[Issue] = []

        # Tracks imports: bound_name -> (line, col, import_node, alias_node)
        self.imports: Dict[str, Tuple[int, int, ast.AST, ast.alias]] = {}
        # Tracks occurrences of names used in Load context
        self.name_loads: Set[str] = set()
        # Tracks occurrences of attribute access
        self.attribute_loads: Set[str] = set()

        # Tracks defined functions: name -> {node, line, end_line, is_method}
        self.functions: Dict[str, Dict[str, Any]] = {}
        # Tracks function names referenced (loads or attributes)
        self.referenced_names: Set[str] = set()
        # Reference line numbers for each name: name -> list of lines where it was referenced
        self.name_reference_lines: Dict[str, List[int]] = {}

    def add_name_reference(self, name: str, line: int):
        self.referenced_names.add(name)
        if name not in self.name_reference_lines:
            self.name_reference_lines[name] = []
        self.name_reference_lines[name].append(line)

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            # import foo.bar as baz -> bound name is baz
            # import foo.bar -> bound name is foo
            bound_name = alias.asname if alias.asname else alias.name.split('.')[0]
            self.imports[bound_name] = (node.lineno, node.col_offset, node, alias)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        for alias in node.names:
            if alias.name == '*':
                # Wildcard import detected
                end_line = getattr(node, "end_lineno", node.lineno)
                self.issues.append(Issue(
                    file_path=self.file_path,
                    line=node.lineno,
                    col=node.col_offset,
                    issue_type="wildcard_import",
                    severity="warning",
                    message=f"Wildcard import 'from {node.module} import *' pollutes namespace.",
                    weight=WEIGHT_WILDCARD_IMPORT,
                    end_line=end_line,
                    context=self.get_line_context(node.lineno)
                ))
                continue
            # from foo import bar as baz -> bound name is baz
            # from foo import bar -> bound name is bar
            bound_name = alias.asname if alias.asname else alias.name
            self.imports[bound_name] = (node.lineno, node.col_offset, node, alias)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, ast.Load):
            self.name_loads.add(node.id)
            self.add_name_reference(node.id, node.lineno)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        # E.g. obj.method_name -> attribute access
        self.attribute_loads.add(node.attr)
        self.add_name_reference(node.attr, node.lineno)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.process_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.process_function(node)

    def process_function(self, node: Any):
        # Skip dunder methods
        if node.name.startswith("__") and node.name.endswith("__"):
            self.generic_visit(node)
            return

        # Skip common entrypoints
        if node.name == "main":
            self.generic_visit(node)
            return

        # Record function details
        end_line = getattr(node, "end_lineno", node.lineno)
        self.functions[node.name] = {
            "node": node,
            "line": node.lineno,
            "end_line": end_line,
        }

        # Check for stub implementations
        self.check_stub_function(node)

        self.generic_visit(node)

    def check_stub_function(self, node: Any):
        # A stub is a function with body containing only pass, Ellipsis, raise NotImplementedError
        body = node.body
        is_stub = False
        reason = ""

        if len(body) == 1:
            stmt = body[0]
            if isinstance(stmt, ast.Pass):
                is_stub = True
                reason = "contains only 'pass'"
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
                is_stub = True
                reason = "contains only '...'"
            elif isinstance(stmt, ast.Raise):
                if isinstance(stmt.exc, ast.Name) and stmt.exc.id == "NotImplementedError":
                    is_stub = True
                    reason = "raises 'NotImplementedError'"
                elif isinstance(stmt.exc, ast.Call) and isinstance(stmt.exc.func, ast.Name) and stmt.exc.func.id == "NotImplementedError":
                    is_stub = True
                    reason = "raises 'NotImplementedError'"
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                # A function containing only a docstring
                is_stub = True
                reason = "contains only a docstring"

        if is_stub:
            self.issues.append(Issue(
                file_path=self.file_path,
                line=node.lineno,
                col=node.col_offset,
                issue_type="stub_function",
                severity="warning",
                message=f"Function '{node.name}' appears to be an empty stub ({reason}).",
                weight=WEIGHT_STUB_FUNCTION,
                end_line=getattr(node, "end_lineno", node.lineno),
                context=self.get_line_context(node.lineno)
            ))

    def visit_Assign(self, node: ast.Assign):
        self.check_placeholder_assignment(node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        self.check_placeholder_assignment(node)
        self.generic_visit(node)

    def check_placeholder_assignment(self, node: Any):
        # Extract string value if it's a constant
        if not node.value or not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            return

        val_str = node.value.value
        targets = []
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    targets.append(t.id)
                elif isinstance(t, ast.Attribute):
                    targets.append(t.attr)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                targets.append(node.target.id)
            elif isinstance(node.target, ast.Attribute):
                targets.append(node.target.attr)

        placeholder_regexes = [
            r"(?i)your_.*_here",
            r"(?i)insert_.*",
            r"(?i)enter_.*",
            r"(?i)placeholder",
            r"(?i)change[_-]?me",
            r"(?i)todo",
            r"(?i)<.*>",
            r"(?i)\[.*\]",
            r"(?i)your-.*",
            r"^your_api_key$",
            r"^your_token$",
            r"^password123$",
        ]

        for target in targets:
            is_placeholder = False
            # Check value against regexes
            for r in placeholder_regexes:
                if re.search(r, val_str):
                    is_placeholder = True
                    break
            
            # Check key name + typical suspicious placeholder values
            suspicious_keys = {"key", "token", "secret", "pass", "pwd", "credential", "url"}
            any_suspicious_key = any(sk in target.lower() for sk in suspicious_keys)
            
            if any_suspicious_key:
                # If key is key/token/secret and value is very typical dummy text
                dummy_texts = {"your", "insert", "placeholder", "enter", "todo", "example", "here", "change", "secret"}
                if any(dt in val_str.lower() for dt in dummy_texts) or len(val_str) < 5 or val_str.strip() == "":
                    is_placeholder = True

            if is_placeholder:
                self.issues.append(Issue(
                    file_path=self.file_path,
                    line=node.lineno,
                    col=node.col_offset,
                    issue_type="placeholder_var",
                    severity="error",
                    message=f"Placeholder value '{val_str}' assigned to variable/attribute '{target}'.",
                    weight=WEIGHT_PLACEHOLDER_VAR,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    context=self.get_line_context(node.lineno)
                ))

    def get_line_context(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.source_lines):
            return self.source_lines[lineno - 1].strip()
        return ""

    def analyze(self):
        # Run visitor to collect all imports, functions, and references
        self.visit(ast.parse(self.source_code))

        # 1. Unused Imports check
        # An import is unused if its bound name is never in name_loads (except inside imports themselves)
        # Wait, if we import 'foo', but the only reference is the import statement itself.
        # Since ast.alias / ast.Import nodes are visited, we should make sure that the name loads count references.
        # Let's check which bound names have 0 references in the loads.
        for bound_name, (line, col, node, alias) in self.imports.items():
            if bound_name not in self.name_loads:
                # Check if it is inside __all__
                is_in_all = False
                for n in ast.walk(ast.parse(self.source_code)):
                    if isinstance(n, ast.Assign):
                        for t in n.targets:
                            if isinstance(t, ast.Name) and t.id == "__all__":
                                if isinstance(n.value, (ast.List, ast.Tuple, ast.Set)):
                                    for elt in n.value.elts:
                                        if isinstance(elt, ast.Constant) and elt.value == bound_name:
                                            is_in_all = True
                
                if not is_in_all:
                    name_desc = alias.name
                    if alias.asname:
                        name_desc = f"{alias.name} as {alias.asname}"
                    self.issues.append(Issue(
                        file_path=self.file_path,
                        line=line,
                        col=col,
                        issue_type="unused_import",
                        severity="warning",
                        message=f"Unused import '{name_desc}'.",
                        weight=WEIGHT_UNUSED_IMPORT,
                        end_line=getattr(node, "end_lineno", line),
                        context=self.get_line_context(line)
                    ))

        # 2. Dead Functions check
        for func_name, info in self.functions.items():
            ref_lines = self.name_reference_lines.get(func_name, [])
            
            # An external reference is any reference outside the function's own body range
            external_refs = [r_line for r_line in ref_lines if not (info["line"] <= r_line <= info["end_line"])]
            
            if len(external_refs) == 0:
                self.issues.append(Issue(
                    file_path=self.file_path,
                    line=info["line"],
                    col=info["node"].col_offset,
                    issue_type="dead_function",
                    severity="warning",
                    message=f"Function '{func_name}' is defined but never called or referenced externally.",
                    weight=WEIGHT_DEAD_FUNCTION,
                    end_line=info["end_line"],
                    context=self.get_line_context(info["line"])
                ))


def scan_comments_for_placeholders(source_code: str, file_path: str) -> List[Issue]:
    issues = []
    lines = source_code.splitlines()
    ai_comment_patterns = [
        (r"(?i)#\s*todo:\s*(implement|replace|fill)", "AI placeholder comment: TODO implementation detail"),
        (r"(?i)#\s*generated\s*by\s*(copilot|ai|chatgpt)", "AI leftover comment: 'Generated by AI'"),
        (r"(?i)#\s*insert\s*.*\s*here", "AI placeholder comment: insert indicator"),
        (r"(?i)#\s*change\s*me", "AI placeholder comment: 'change me'"),
    ]
    for idx, line in enumerate(lines, 1):
        # We only want to match actual comment lines or parts of lines that are comments
        comment_match = re.search(r"#.*", line)
        if comment_match:
            comment_text = comment_match.group(0)
            for pattern, message in ai_comment_patterns:
                if re.search(pattern, comment_text):
                    issues.append(Issue(
                        file_path=file_path,
                        line=idx,
                        col=comment_match.start(),
                        issue_type="placeholder_var", # Treat comment placeholders similarly to variable placeholders
                        severity="warning",
                        message=message,
                        weight=5, # lighter weight for comments
                        context=line.strip()
                    ))
    return issues


def detect_issues(source_code: str, file_path: str) -> List[Issue]:
    # AST analysis
    try:
        analyzer = CodeAnalyzer(file_path, source_code)
        analyzer.analyze()
        issues = analyzer.issues
    except SyntaxError as e:
        # If there's a syntax error, we report it as a critical failure
        issues = [Issue(
            file_path=file_path,
            line=e.lineno or 1,
            col=e.offset or 1,
            issue_type="syntax_error",
            severity="error",
            message=f"Syntax Error: {e.msg}",
            weight=50,
            context=e.text.strip() if e.text else ""
        )]
        return issues

    # Comments analysis
    comment_issues = scan_comments_for_placeholders(source_code, file_path)
    issues.extend(comment_issues)
    
    # Sort issues by line number
    issues.sort(key=lambda x: x.line)
    return issues
