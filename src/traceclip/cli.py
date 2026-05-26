import os
import sys
import argparse
from pathlib import Path
from typing import List

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from traceclip.detector import detect_issues
from traceclip.fixer import fix_file

console = Console()

def find_py_files(paths: List[str]) -> List[Path]:
    py_files = []
    ignored_dirs = {".git", "venv", ".venv", "env", "__pycache__", ".pytest_cache", "build", "dist", ".egg-info"}

    for path_str in paths:
        path = Path(path_str)
        if path.is_file():
            if path.suffix == ".py":
                py_files.append(path)
        elif path.is_dir():
            for root, dirs, files in os.walk(path):
                # Prune ignored directories in-place to avoid traversing them
                dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
                for file in files:
                    if file.endswith(".py"):
                        py_files.append(Path(root) / file)
    return sorted(list(set(py_files)))

def format_score(score: int) -> Text:
    if score >= 90:
        return Text(f"{score}/100 (IMMACULATE VIBES)", style="bold green")
    elif score >= 70:
        return Text(f"{score}/100 (PASSABLE VIBES)", style="bold yellow")
    else:
        return Text(f"{score}/100 (BAD VIBES)", style="bold red")

def main():
    parser = argparse.ArgumentParser(
        description="Traceclip (vibecheck): The AI-Generated Code Sanitizer."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["."],
        help="Files or directories to scan (default: current directory)."
    )
    parser.add_argument(
        "--fix",
        "-f",
        action="store_true",
        help="Automatically sanitize and fix issues in-place."
    )
    parser.add_argument(
        "--min-score",
        "-m",
        type=int,
        default=0,
        help="Minimum allowed Vibe Score. Returns exit code 1 if any file scores lower."
    )

    args = parser.parse_args()
    py_files = find_py_files(args.paths)

    if not py_files:
        console.print("[bold red]Error:[/] No Python files found to scan.", style="red")
        sys.exit(1)

    console.print(Panel(
        "[bold cyan]Traceclip Vibecheck[/] - Linting wrapper for AI-generated code\n"
        "[dim]Hunts down unused imports, dead functions, stub methods, and placeholder secrets.[/]",
        border_style="cyan"
    ))

    all_passed = True
    total_issues = 0
    total_files = len(py_files)
    scores = []

    for file_path in py_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            console.print(f"[bold red]Error reading file {file_path}:[/] {e}")
            continue

        # Detect issues
        issues = detect_issues(content, str(file_path))
        
        # Calculate Vibe Score
        deductions = sum(issue.weight for issue in issues)
        score = max(0, 100 - deductions)
        scores.append(score)
        total_issues += len(issues)

        # Print report
        relative_path = os.path.relpath(file_path, os.getcwd())
        console.print(f"\n[bold underline]{relative_path}[/]")

        if not issues:
            console.print("  [bold green]✓[/] Vibe check passed! Code is clean and tidy. (Score: 100/100)")
            continue

        # Display issues in a table
        table = Table(show_header=True, header_style="bold magenta", box=None, padding=(0, 2))
        table.add_column("Line:Col", justify="right", style="dim")
        table.add_column("Type", style="bold")
        table.add_column("Message")
        table.add_column("Context", style="italic dim")

        for issue in issues:
            sev_style = "bold red" if issue.severity == "error" else "yellow"
            issue_type_formatted = f"[{sev_style}]{issue.issue_type.replace('_', ' ').title()}[/]"
            table.add_row(
                f"{issue.line}:{issue.col}",
                issue_type_formatted,
                issue.message,
                issue.context
            )
        
        console.print(table)
        console.print(f"  [bold]Vibe Score:[/] ", end="")
        console.print(format_score(score))

        # Enforce min-score check
        if score < args.min_score:
            all_passed = False

        # Run Auto-Fix if requested
        if args.fix and len([i for i in issues if i.issue_type not in ("syntax_error", "wildcard_import")]) > 0:
            console.print(f"  [cyan]Applying autofixes to {relative_path}...[/]")
            fixed_content = fix_file(content, str(file_path))
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(fixed_content)
                console.print(f"  [bold green]✓[/] Sanitized and saved!")
                # Recalculate score for summary print
                fixed_issues = detect_issues(fixed_content, str(file_path))
                fixed_deductions = sum(issue.weight for issue in fixed_issues)
                fixed_score = max(0, 100 - fixed_deductions)
                scores[-1] = fixed_score
                console.print(f"  [bold]New Vibe Score:[/] ", end="")
                console.print(format_score(fixed_score))
            except Exception as e:
                console.print(f"  [bold red]Failed to write fixes:[/] {e}")

    # Summary Panel
    avg_score = int(sum(scores) / len(scores)) if scores else 100
    summary_style = "bold green" if avg_score >= 90 else "bold yellow" if avg_score >= 70 else "bold red"
    
    console.print("\n" + "="*50)
    console.print(f"[bold cyan]Summary Report:[/]")
    console.print(f"  Files scanned: {total_files}")
    console.print(f"  Total issues found: {total_issues}")
    console.print(f"  Average Vibe Score: ", end="")
    console.print(format_score(avg_score))
    console.print("="*50)

    if not all_passed:
        console.print("\n[bold red]✖ vibecheck failed:[/] One or more files scored below the minimum threshold of --min-score.", style="red")
        sys.exit(1)

if __name__ == "__main__":
    main()
