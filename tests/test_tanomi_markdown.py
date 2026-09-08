import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")
def test_tanomi_markdown_dom_and_card() -> None:
    result = subprocess.run(
        ["node", str(ROOT / "tests/tanomi_markdown_dom.cjs")],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DOM and card checks passed" in result.stdout


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_tanomi_markdown_native_structure_and_content(tmp_path: Path) -> None:
    main = tmp_path / "main.swift"
    main.write_text(
        r'''
import Foundation

func flatten(_ blocks: [MarkdownBlock]) -> [MarkdownBlock] {
    blocks.flatMap { [$0] + flatten($0.children) }
}
func content(_ blocks: [MarkdownBlock]) -> String {
    flatten(blocks).map { String($0.text.characters) }.joined(separator: "\n")
}
let source = """
# 実装結果

日本語の**太字**と*斜体*、`inline_code`、[参照](https://example.com/docs)です。
改行後の説明も読みやすく表示します。

3. 検証を実行しました。
   - 子項目の確認
   - **入れ子の強調**
4. 次の作業へ進めます。

> 補足の引用です。
> 二行目の引用です。

```swift
let message = "こんにちは"
print(message)
```

| 対象 | 結果 | 件数 |
| :--- | :---: | ---: |
| iPhone | 成功 | 12 |
| macOS | | 8 |

<script>本文として表示します</script>

![画像の代替文字](https://example.com/image.png)
[危険なリンク](javascript:alert(1))は開きません。

## 未完の入力

**閉じていない強調 と [途中のリンク](
"""
let doc = MarkdownDocument(source)
let all = flatten(doc.blocks)
precondition(all.contains {
    $0.kind == .header(level: 1) && String($0.text.characters) == "実装結果"
})
precondition(all.contains { $0.kind == .orderedList })
precondition(all.contains { $0.kind == .listItem(ordinal: 3) })
let list = all.first { $0.kind == .orderedList }!
precondition(flatten(list.children).contains { $0.kind == .unorderedList })
precondition(all.contains { $0.kind == .blockQuote })
let code = all.first { if case .codeBlock = $0.kind { return true }; return false }!
precondition(code.kind == .codeBlock(languageHint: "swift"))
precondition(String(code.text.characters) == "let message = \"こんにちは\"\nprint(message)\n")
let table = all.first { if case .table = $0.kind { return true }; return false }!
if case .table(let columns) = table.kind {
    precondition(columns.count == 3 && columns[2].alignment == .right)
}
let row = table.children.first { $0.kind == .tableRow(rowIndex: 2) }!
precondition(row.children.contains {
    $0.kind == .tableCell(columnIndex: 2) && String($0.text.characters) == "8"
})
precondition(!row.children.contains { $0.kind == .tableCell(columnIndex: 1) })
let emptyRowTable = MarkdownDocument("| A | B |\n|---|---|\n| | |\n| C | D |")
let tableRows = emptyRowTable.blocks[0].children
precondition(tableRows.count == 3 && tableRows[1].kind == .tableRow(rowIndex: 1))
precondition(tableRows[1].children.isEmpty)
precondition(content(all).contains("<script>本文として表示します</script>"))
precondition(content(all).contains("**閉じていない強調"))
let runs = all.flatMap { Array($0.text.runs) }
precondition(runs.contains { $0.inlinePresentationIntent?.contains(.stronglyEmphasized) == true })
precondition(runs.contains { $0.inlinePresentationIntent?.contains(.emphasized) == true })
precondition(runs.contains { $0.inlinePresentationIntent?.contains(.code) == true })
precondition(runs.contains { $0.link?.absoluteString == "https://example.com/docs" })
precondition(runs.allSatisfy {
    $0.imageURL == nil && ($0.link == nil || MarkdownDocument.isSafeLink($0.link!))
})
precondition(doc.canExpand && doc.preview.count < doc.blocks.count)
precondition(doc.preview == Array(doc.blocks.prefix(doc.preview.count)))

// A short message with more than eight lines must never become unreachable.
let shortSource = (1...12).map { "短い行 \($0)" }.joined(separator: "\n")
let shortLines = MarkdownDocument(shortSource)
precondition(content(shortLines.blocks) == shortSource)
precondition(!shortLines.canExpand && shortLines.preview == shortLines.blocks)
let paragraphs = MarkdownDocument("一\n\n二\n\n三\n\n四")
precondition(paragraphs.canExpand && paragraphs.preview.count == 3)
let long = String(repeating: "長い本文", count: 3000)
let longDocument = MarkdownDocument(long)
precondition(content(longDocument.preview) == long && !longDocument.canExpand)
precondition(MarkdownDocument("").blocks.isEmpty)
precondition(content(MarkdownDocument("![](https://example.com/x)").blocks) == "画像")
let incompleteCode = MarkdownDocument("```\n未完コード\n次行")
precondition(content(incompleteCode.blocks).contains("未完コード\n次行"))
let hostile = MarkdownDocument(
    "[悪](javascript:alert(1)) [data](data:text/html,x) [相対](/api/tasks)"
)
precondition(flatten(hostile.blocks).allSatisfy { $0.text.runs.allSatisfy { $0.link == nil } })
let unsafe = ["file:///tmp/test", "javascript:alert(1)",
              "https://user:pass@example.com", "/api/tasks"]
for value in unsafe {
    precondition(!MarkdownDocument.isSafeLink(URL(string: value)!))
}
precondition(MarkdownDocument.isSafeLink(URL(string: "HTTPS://example.com/docs")!))

print("tanomi Markdown native structure and content checks passed")
''',
        encoding="utf-8",
    )
    binary = tmp_path / "markdown-test"
    compile_result = subprocess.run(
        [
            "xcrun", "swiftc", "-D", "DEBUG",
            "-module-cache-path", str(tmp_path / "module-cache"),
            str(ROOT / "ios/DailyReader/DailyReader/MarkdownDocument.swift"),
            str(main), "-o", str(binary),
        ],
        capture_output=True, text=True, check=False,
    )
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "native structure and content checks passed" in result.stdout
