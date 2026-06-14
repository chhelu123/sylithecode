"""
Project auto-detection — inspired by Claude Code's detectRepository.
Reads package.json / requirements.txt / pom.xml / build.gradle / go.mod
and injects project context into every system prompt.
"""
import json
from pathlib import Path


def detect_project(cwd: str = ".") -> dict:
    """Detect project type and metadata from common config files."""
    root = Path(cwd)
    info = {
        "type":     "unknown",
        "language": "unknown",
        "name":     root.name,
        "version":  "",
        "deps":     [],
        "scripts":  [],
        "test_cmd": "",
        "run_cmd":  "",
        "framework": "",
    }

    # Node.js
    pkg = root / "package.json"
    if pkg.exists():
        try:
            d = json.loads(pkg.read_text(encoding="utf-8"))
            info.update({
                "type":     "node",
                "language": "javascript" if not (root / "tsconfig.json").exists() else "typescript",
                "name":     d.get("name", root.name),
                "version":  d.get("version", ""),
                "deps":     list(d.get("dependencies", {}).keys())[:20],
                "scripts":  list(d.get("scripts", {}).keys()),
                "test_cmd": "npm test",
                "run_cmd":  "npm start",
                "framework": _detect_node_framework(d),
            })
        except Exception:
            pass
        return info

    # Python
    for pyfile in ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile"]:
        if (root / pyfile).exists():
            info["type"]     = "python"
            info["language"] = "python"
            info["test_cmd"] = "pytest"
            info["run_cmd"]  = "python app.py"

            if pyfile == "requirements.txt":
                lines = (root / pyfile).read_text(encoding="utf-8").splitlines()
                info["deps"] = [l.split("==")[0].split(">=")[0].strip()
                                for l in lines if l.strip() and not l.startswith("#")][:20]
                info["framework"] = _detect_python_framework(info["deps"])

            elif pyfile == "pyproject.toml":
                try:
                    import tomllib
                    with open(root / pyfile, "rb") as f:
                        d = tomllib.load(f)
                    proj = d.get("project", {})
                    info["name"]    = proj.get("name", root.name)
                    info["version"] = proj.get("version", "")
                    info["deps"]    = [str(d).split("[")[0].split(">=")[0].split("==")[0].strip()
                                       for d in proj.get("dependencies", [])][:20]
                    info["framework"] = _detect_python_framework(info["deps"])
                    scripts = d.get("project", {}).get("scripts", {})
                    if scripts:
                        info["run_cmd"] = f"python -m {list(scripts.values())[0]}"
                except Exception:
                    pass

            return info

    # Java / Maven
    if (root / "pom.xml").exists():
        info.update({
            "type":     "java",
            "language": "java",
            "test_cmd": "mvn test",
            "run_cmd":  "mvn spring-boot:run",
            "framework": "Spring Boot",
        })
        return info

    # Java / Gradle
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        kts = (root / "build.gradle.kts").exists()
        info.update({
            "type":     "java",
            "language": "kotlin" if kts else "java",
            "test_cmd": "./gradlew test",
            "run_cmd":  "./gradlew bootRun",
            "framework": "Spring Boot",
        })
        return info

    # Go
    if (root / "go.mod").exists():
        info.update({
            "type":     "go",
            "language": "go",
            "test_cmd": "go test ./...",
            "run_cmd":  "go run .",
        })
        return info

    # Rust
    if (root / "Cargo.toml").exists():
        info.update({
            "type":     "rust",
            "language": "rust",
            "test_cmd": "cargo test",
            "run_cmd":  "cargo run",
        })
        return info

    # Flutter / Dart
    if (root / "pubspec.yaml").exists():
        info.update({
            "type":     "flutter",
            "language": "dart",
            "test_cmd": "flutter test",
            "run_cmd":  "flutter run",
            "framework": "Flutter",
        })
        return info

    return info


def _detect_node_framework(pkg: dict) -> str:
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    if "next" in deps:       return "Next.js"
    if "react" in deps:      return "React"
    if "@angular/core" in deps: return "Angular"
    if "vue" in deps:        return "Vue.js"
    if "express" in deps:    return "Express.js"
    if "fastify" in deps:    return "Fastify"
    if "nestjs" in deps:     return "NestJS"
    return ""


def _detect_python_framework(deps: list[str]) -> str:
    deps_lower = [d.lower() for d in deps]
    if "django" in deps_lower:   return "Django"
    if "flask" in deps_lower:    return "Flask"
    if "fastapi" in deps_lower:  return "FastAPI"
    if "streamlit" in deps_lower: return "Streamlit"
    if "celery" in deps_lower:   return "Celery"
    return ""


def project_context_string(cwd: str = ".") -> str:
    """Returns a string to inject into the system prompt about this project."""
    info = detect_project(cwd)
    if info["type"] == "unknown":
        return ""

    lines = [f"\n\n## Auto-detected Project Context"]
    lines.append(f"- **Project**: {info['name']}")
    lines.append(f"- **Language**: {info['language']}")
    if info.get("framework"):
        lines.append(f"- **Framework**: {info['framework']}")
    if info.get("version"):
        lines.append(f"- **Version**: {info['version']}")
    if info.get("test_cmd"):
        lines.append(f"- **Test command**: `{info['test_cmd']}`")
    if info.get("run_cmd"):
        lines.append(f"- **Run command**: `{info['run_cmd']}`")
    if info.get("deps"):
        lines.append(f"- **Key dependencies**: {', '.join(info['deps'][:10])}")
    if info.get("scripts"):
        lines.append(f"- **Scripts**: {', '.join(info['scripts'][:8])}")

    return "\n".join(lines)
