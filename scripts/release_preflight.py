#!/usr/bin/env python3
"""正式发布前的**发布卫生**预检。

把"发布卫生收尾"里那几条人工检查固化成一条命令，避免下一个版本再手工重来一遍。
本脚本**只读**：不修改工作区、不写文件、不提交、不打 tag。

检查项（按执行顺序）：

    1. `git diff --check` 干净（无行尾空白 / 文件末尾多余空行）
    2. `CHANGELOG.md` 只有一个 `## [Unreleased]` 区块；版本区块列表可读
    3. ref 树里没有硬编码的密钥字面量（常见厂商前缀）
    4. ref 树里没有本机绝对家目录路径（`/Users/<name>`、`/home/<name>`、`C:\\Users\\<name>`）
    5. 本机凭据文件（`outputs/provider_config.json`）确实被 git 忽略
    6. 没有任何 `outputs/` 路径被跟踪或已暂存
    7. 已构建的 `dist/*.whl` / `dist/*.tar.gz` 里不含 `outputs/` 或 `provider_config`
    8. 已构建产物与 **ref（默认 HEAD）逐字节一致** —— 拦住"改了代码忘了重新打包"

第 3、4、8 项读的都是 **ref 树，不是工作区**：要发布的单元是提交。这一点搞反了会
**两个方向都错**——工作区里已经删掉的泄漏，提交里还在（**漏报**，发布带着它出去）；
工作区里新引入、尚未提交的泄漏（**误报**，挡住一个根本不含它的发布）。
工作区的脏状态由第 8 项末尾的 WARN 单独说明，不混进上面的结论。

用法：

    python scripts/release_preflight.py                 # 以 HEAD 为基准
    python scripts/release_preflight.py --ref v0.4.0    # 以某个 tag 为基准（发布时推荐）
    python scripts/release_preflight.py --quiet         # 只输出失败项

退出码：全部通过 0，有 FAIL 为 1（ref 不存在也算 FAIL）。WARN 不影响退出码。

注意：本脚本**不做** commit / tag / push。形成可发布的 commit 与 tag 是人的决定，
因为它需要判断哪些改动属于同一个发布单元（工作区可能同时存在多条工作流）。
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
DIST = ROOT / "dist"
CREDENTIAL_CANDIDATES = [
    "outputs/provider_config.json",
]

# 常见厂商密钥前缀。用否定后视断言要求真正的边界，
# 否则文件名里的 "ta|sk-step-1" 会被误报成 sk- 密钥（实测踩过）。
SECRET_PATTERNS = [
    (r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{20,}", "openai 风格 sk-"),
    (r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_\-]{20,}", "anthropic"),
    (r"(?<![A-Za-z0-9])AIza[A-Za-z0-9_\-]{30,}", "google api key"),
    (r"(?<![A-Za-z0-9])(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{30,}", "github token"),
    (r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}", "aws key id"),
    (r"(?<![A-Za-z0-9])(?:xox[baprs])-[A-Za-z0-9\-]{20,}", "slack token"),
    (r"(?<![A-Za-z0-9])(?:hf_|glpat-)[A-Za-z0-9_\-]{20,}", "huggingface/gitlab"),
]

# 只作提示、不作失败：测试夹具里合法的长字符串会命中，交给人工判断。
SOFT_PATTERNS = [
    (r"""(?:api[_-]?key|apikey|secret|token)\s*[=:]\s*["']([A-Za-z0-9_\-]{24,})["']""",
     "长字面量赋给 key 类名字"),
]

# 本机绝对家目录路径：泄露用户名与个人目录结构，且**不会让任何测试失败**——
# 所以只能靠门禁守。第三方包的构建元数据里也带 CI 机器人的家目录路径，但那些文件
# 不受版本控制，不在扫描范围内。合法的示例请用 `<user>` 之类的占位符。
HOME_PATH_PATTERNS = [
    (r"/Users/[A-Za-z0-9._\-]+", "macOS 家目录"),
    (r"/home/[A-Za-z0-9._\-]+", "Linux 家目录"),
    (r"/var/folders/[A-Za-z0-9._\-]+/", "macOS 临时目录"),
    (r"[Cc]:\\Users\\[A-Za-z0-9._\-]+", "Windows 家目录"),
]

# Verbatim, publicly redistributed RWS fixtures. Exempt only these exact bytes:
# an edited file must be scanned again. Provenance and hashes are in the README.
_PUBLIC_FIXTURE_HASHES = {
    "tests/fixtures/sdlxliff/TTSExample.docx.sdlxliff":
        "ab3fcbcb32a91ad894c25636be6970c70c218f674e3d57b87c8eaf4b909b8710",
    "tests/fixtures/sdlxliff/quickplace.docx.sdlxliff":
        "85e67619be7594ff80aaae03f17adbbe85f048680f39b74b31e36e19fff1cb4b",
}


class Report:
    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet
        self.failures = 0
        self.warnings = 0

    def ok(self, label: str, detail: str = "") -> None:
        if not self.quiet:
            print(f"  OK    {label}" + (f" — {detail}" if detail else ""))

    def fail(self, label: str, detail: str = "") -> None:
        self.failures += 1
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))

    def warn(self, label: str, detail: str = "") -> None:
        self.warnings += 1
        print(f"  WARN  {label}" + (f" — {detail}" if detail else ""))

    def section(self, title: str) -> None:
        if not self.quiet:
            print(f"\n{title}")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


def _tracked_files() -> list[str]:
    out = _git("ls-files").stdout
    return [line for line in out.splitlines() if line.strip()]


def _ref_files(ref: str) -> list[str]:
    """ref 树里的全部文件路径（**不是**工作区）。"""
    out = _git("ls-tree", "-r", "--name-only", ref).stdout
    return [line for line in out.splitlines() if line.strip()]


def _ref_text(ref: str, rel: str) -> str:
    """ref 树里某个文件的内容，按文本解码（二进制以 `ignore` 兜底）。

    这里**故意不跳过二进制**：截图 PNG 的 tEXt 元数据里同样可能带着本机路径，
    那也是一种泄漏，不该因为"它是一张图片"就被放过。
    """
    raw = _ref_blob(ref, rel)
    return "" if raw is None else raw.decode("utf-8", "ignore")


def check_whitespace(rep: Report) -> None:
    rep.section("[1] git diff --check")
    proc = _git("diff", "--check")
    if proc.returncode == 0:
        rep.ok("无行尾空白 / 文件末尾多余空行")
        return
    offenders = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    rep.fail(f"{len(offenders)} 处空白问题", offenders[0] if offenders else "")
    for ln in offenders[1:6]:
        print(f"          {ln}")


def check_changelog(rep: Report) -> None:
    rep.section("[2] CHANGELOG 结构")
    if not CHANGELOG.exists():
        rep.fail("CHANGELOG.md 不存在")
        return
    text = CHANGELOG.read_text(encoding="utf-8")

    unreleased = [m for m in re.finditer(r"^##\s+\[Unreleased\]", text, re.M)]
    if len(unreleased) == 1:
        rep.ok("只有一个 `## [Unreleased]` 区块")
    elif not unreleased:
        rep.warn("没有 `## [Unreleased]` 区块", "可能已全部并入某个版本号")
    else:
        lines = [text[: m.start()].count("\n") + 1 for m in unreleased]
        rep.fail(f"有 {len(unreleased)} 个 `## [Unreleased]` 区块",
                 f"行 {', '.join(map(str, lines))}")

    headings = re.findall(r"^##\s+(.+)$", text, re.M)
    if not rep.quiet:
        print(f"  版本区块（{len(headings)} 个）：")
        for h in headings[:8]:
            print(f"          {h}")
        if len(headings) > 8:
            print(f"          … 另有 {len(headings) - 8} 个")


def check_secrets(rep: Report, ref: str) -> None:
    rep.section(f"[3] {ref} 树中的密钥字面量")
    hard: list[tuple[str, int, str]] = []
    soft: list[tuple[str, int, str]] = []
    for rel in _ref_files(ref):
        text = _ref_text(ref, rel)
        for pat, label in SECRET_PATTERNS:
            for m in re.finditer(pat, text):
                hard.append((rel, text[: m.start()].count("\n") + 1, label))
        for pat, label in SOFT_PATTERNS:
            for m in re.finditer(pat, text):
                soft.append((rel, text[: m.start()].count("\n") + 1, label))

    if hard:
        rep.fail(f"{len(hard)} 处疑似真实密钥", f"{hard[0][0]}:{hard[0][1]} [{hard[0][2]}]")
        for rel, line, label in hard[1:6]:
            print(f"          {rel}:{line} [{label}]")
    else:
        rep.ok("无厂商前缀形式的密钥")

    if soft:
        rep.warn(f"{len(soft)} 处长字面量赋给 key 类名字（需人工确认是否为夹具）",
                 f"{soft[0][0]}:{soft[0][1]}")
    else:
        rep.ok("无长字面量赋给 key 类名字")


def check_no_home_paths(rep: Report, ref: str) -> None:
    """本机绝对家目录路径不该出现在仓库里。

    这类内容不会让任何测试失败、不影响运行、也不报错——它只是静静地躺在文档里，
    等着随仓库一起公开。正因为它**不制造任何症状**，靠人记住是守不住的。

    扫描对象是 **ref 树**，不是工作区：泄漏一旦进了提交，就从工作区里删掉也还在提交里。
    实测这个仓库 48 个提交里有 47 个带着 `/Users/<name>`——"我在工作区改好了"
    和"发布里没有它"是两件事。
    """
    rep.section(f"[4] {ref} 树中的本机绝对路径")
    hits: list[tuple[str, int, str]] = []
    for rel in _ref_files(ref):
        expected = _PUBLIC_FIXTURE_HASHES.get(rel)
        if expected:
            blob = _ref_blob(ref, rel)
            if blob is not None and hashlib.sha256(blob).hexdigest() == expected:
                continue
        text = _ref_text(ref, rel)
        for pat, label in HOME_PATH_PATTERNS:
            for m in re.finditer(pat, text):
                line = text[: m.start()].count("\n") + 1
                hits.append((rel, line, f"{label} {m.group(0)}"))

    if hits:
        rep.fail(f"{len(hits)} 处本机绝对路径", f"{hits[0][0]}:{hits[0][1]} [{hits[0][2]}]")
        for rel, line, detail in hits[1:6]:
            print(f"          {rel}:{line} [{detail}]")
        print("          修法：相对路径 / `$(git rev-parse --show-toplevel)` / <占位符>")
    else:
        rep.ok("无本机绝对家目录路径")


def check_credential_ignored(rep: Report) -> None:
    rep.section("[5] 本机凭据文件是否被忽略")
    for rel in CREDENTIAL_CANDIDATES:
        path = ROOT / rel
        ignored = _git("check-ignore", "-q", rel).returncode == 0
        if not path.exists():
            rep.ok(f"{rel} 不存在（无需处理）")
        elif ignored:
            rep.ok(f"{rel} 已被 git 忽略")
        else:
            rep.fail(f"{rel} 存在但**未**被 git 忽略", "发布前必须加入 .gitignore")


def check_no_outputs_tracked(rep: Report) -> None:
    rep.section("[6] outputs/ 是否进入版本控制")
    tracked = [f for f in _tracked_files() if f.startswith(("outputs/", "exports/"))]
    staged = [
        ln for ln in _git("diff", "--cached", "--name-only").stdout.splitlines()
        if ln.startswith(("outputs/", "exports/"))
    ]
    if tracked:
        rep.fail(f"{len(tracked)} 个 outputs/ 路径已被跟踪", tracked[0])
    elif staged:
        rep.fail(f"{len(staged)} 个 outputs/ 路径已暂存", staged[0])
    else:
        rep.ok("没有任何 outputs/ 路径被跟踪或暂存")


def check_artifacts(rep: Report) -> None:
    rep.section("[7] 已构建产物内容")
    if not DIST.exists():
        rep.warn("dist/ 不存在", "尚未构建，跳过")
        return
    wheels = sorted(DIST.glob("*.whl"))
    sdists = sorted(DIST.glob("*.tar.gz"))
    if not wheels and not sdists:
        rep.warn("dist/ 下没有 wheel / sdist", "跳过")
        return

    import tarfile
    import zipfile

    def suspicious(names: list[str]) -> list[str]:
        return [n for n in names
                if "provider_config" in n or "/outputs/" in n
                or n.startswith("outputs/")]

    for whl in wheels:
        try:
            with zipfile.ZipFile(whl) as z:
                hits = suspicious(z.namelist())
        except (OSError, zipfile.BadZipFile) as exc:
            rep.fail(f"{whl.name} 无法读取", str(exc))
            continue
        if hits:
            rep.fail(f"{whl.name} 含敏感路径", hits[0])
        else:
            rep.ok(f"{whl.name} 不含 outputs/ 或 provider_config")

    for sd in sdists:
        try:
            with tarfile.open(sd) as t:
                hits = suspicious(t.getnames())
        except (OSError, tarfile.TarError) as exc:
            rep.fail(f"{sd.name} 无法读取", str(exc))
            continue
        if hits:
            rep.fail(f"{sd.name} 含敏感路径", hits[0])
        else:
            rep.ok(f"{sd.name} 不含 outputs/ 或 provider_config")


def _ref_blob(ref: str, rel: str):
    """取 `ref` 里某个文件的内容；该文件不在 ref 里则返回 None。"""
    proc = subprocess.run(
        ["git", "show", f"{ref}:{rel}"], cwd=ROOT, capture_output=True, check=False
    )
    return proc.stdout if proc.returncode == 0 else None


def check_artifacts_match_release(rep: Report, ref: str) -> None:
    """产物里的每个源文件必须与 `ref` 里的同名文件**逐字节相同**。

    比对对象是 **ref（默认 HEAD），不是工作区**——这一点很容易搞错，而且搞错了
    后果很具体：要发布的单元是**提交**，产物必须对得上提交。工作区里的未提交改动
    是另一回事（`git status` 已经显示），在这里报成"产物过期"就是**误报**；
    而一个经常误报的检查会被忽略掉，比没有更糟。

    拦住的是这类发布事故：改了源码、提交了、**却忘了重新打包**——于是 `dist/`
    里躺着旧 wheel。安装、冒烟、导入全都正常，因为旧 wheel 本身是自洽的，
    **只有内容对不上提交**。构建产物不会自己报错。
    """
    rep.section(f"[8] 产物与发布版本（{ref}）一致性")
    if not DIST.exists():
        rep.warn("dist/ 不存在", "跳过")
        return
    artifacts = sorted(DIST.glob("*.whl")) + sorted(DIST.glob("*.tar.gz"))
    if not artifacts:
        rep.warn("dist/ 下没有 wheel / sdist", "跳过")
        return

    for path in artifacts:
        identical = differs = missing = 0
        examples: list[str] = []
        try:
            for rel, blob in _iter_artifact_sources(path):
                committed = _ref_blob(ref, rel)
                if committed is None:
                    missing += 1
                    if len(examples) < 3:
                        examples.append(f"产物里有但 {ref} 没有: {rel}")
                elif committed == blob:
                    identical += 1
                else:
                    differs += 1
                    if len(examples) < 3:
                        examples.append(f"内容与 {ref} 不一致: {rel}")
        except (OSError, ValueError, ImportError) as exc:
            rep.fail(f"{path.name} 无法比对", f"{type(exc).__name__}: {exc}")
            continue

        if differs or missing:
            rep.fail(
                f"{path.name} 与 {ref} 不一致",
                f"不一致 {differs} / 产物多出 {missing}；{'；'.join(examples)}"
                f" —— 多半是改了源码忘了重新打包",
            )
        else:
            rep.ok(f"{path.name} 与 {ref} 一致", f"{identical} 个源文件逐字节一致")

    # 顺带说明工作区状态，避免"产物对得上 HEAD、但我刚改了文件"被误读
    dirty = [
        ln for ln in _git("status", "--porcelain").stdout.splitlines() if ln.strip()
    ]
    if dirty:
        rep.warn(
            f"工作区有 {len(dirty)} 处未提交改动",
            f"产物比对的是 {ref}；这些改动不在产物里，也不在上面的结论里",
        )


def _iter_artifact_sources(path: Path):
    """逐个产出产物里的源文件：(相对路径, 内容)。跳过打包生成物。"""
    import tarfile
    import zipfile

    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.endswith("/") or _is_generated(name):
                    continue
                yield name, zf.read(name)
        return

    with tarfile.open(path) as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split("/", 1)
            if len(parts) < 2:
                continue
            rel = parts[1]
            if _is_generated(rel):
                continue
            extracted = tf.extractfile(member)
            if extracted is not None:
                yield rel, extracted.read()


def _is_generated(rel: str) -> bool:
    if ".dist-info/" in rel or ".egg-info/" in rel:
        return True
    return Path(rel).name in {
        "PKG-INFO", "setup.cfg", "SOURCES.txt", "dependency_links.txt",
        "top_level.txt", "entry_points.txt", "requires.txt",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="发布卫生预检（只读）")
    parser.add_argument("--dist", type=Path, default=DIST, help="构建产物目录")
    parser.add_argument("--quiet", action="store_true", help="只输出失败项")
    parser.add_argument(
        "--ref",
        default="HEAD",
        help="产物比对的基准 revision（默认 HEAD；发布时建议显式给 tag，如 --ref v0.4.0）",
    )
    args = parser.parse_args()
    globals()["DIST"] = args.dist.resolve()

    if _git("rev-parse", "--verify", args.ref).returncode != 0:
        print(f"FAIL  revision `{args.ref}` 不存在 —— 无法比对产物")
        return 1

    rep = Report(args.quiet)
    if not args.quiet:
        print(f"发布卫生预检 — {ROOT}")

    check_whitespace(rep)
    check_changelog(rep)
    check_secrets(rep, args.ref)
    check_no_home_paths(rep, args.ref)
    check_credential_ignored(rep)
    check_no_outputs_tracked(rep)
    check_artifacts(rep)
    check_artifacts_match_release(rep, args.ref)

    print()
    if rep.failures:
        print(f"结果：{rep.failures} 项 FAIL，{rep.warnings} 项 WARN —— 尚不适合正式发布")
        return 1
    print(f"结果：全部通过（{rep.warnings} 项 WARN 需人工确认）")
    print("注意：本脚本不创建 commit / tag —— 那需要人判断发布单元如何切分。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
