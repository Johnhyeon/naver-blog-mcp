"""붙여넣은 표에서 스마트에디터가 열 너비와 가운데 맞춤을 살리는지 본다. 저장하지 않는다."""
import asyncio, os, sys
sys.path.insert(0, "src")
os.environ.setdefault("NAVER_BLOG_ID", "leetkey_lab")
from naver_blog_mcp.session import Session
from naver_blog_mcp.editor import goto_editor, get_editor_frame, write_post

MD = """| 순서 | 볼 것 |
|---|---|
| 1 | SK하이닉스 외국인 매매가 5일 중 4일 순매도에서 바뀌는지 |
| 2 | 삼성전자 외국인 순매수가 사흘째로 이어지는지 |
"""

JS = """() => {
  const out = [];
  document.querySelectorAll('table').forEach(t => {
    const cols = t.querySelectorAll('col');
    out.push('col 태그: ' + cols.length + ' ' + [...cols].map(c => c.getAttribute('width') || c.style.width).join(','));
    t.querySelectorAll('tr').forEach((tr, ri) => {
      out.push('행' + ri + ': ' + [...tr.children].map(c => {
        const cs = getComputedStyle(c);
        return (c.style.width || '-') + '/' + cs.width + '/' + cs.textAlign;
      }).join('  |  '));
    });
  });
  return out;
}"""

async def main():
    async with Session() as ctx:
        page = await ctx.new_page()
        await goto_editor(page, os.environ["NAVER_BLOG_ID"])
        await write_post(page, "표 너비 확인(저장 안 함)", MD)
        frame = await get_editor_frame(page)
        for line in await frame.evaluate(JS):
            print(line)

asyncio.run(main())
