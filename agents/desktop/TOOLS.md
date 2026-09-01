# TOOLS.md — Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to
your setup. It is usage guidance, not availability.

## What Goes Here

Things like:

- SSH hosts and aliases
- API providers / base URLs you commonly use (never the keys themselves)
- Device nicknames, paths, or directories you reach for often
- Anything environment-specific

## Examples

```markdown
### SSH
- home-server → 192.168.1.100, user: admin

### Common paths
- notes → ~/notes
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without
losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.

---

## 这份文件与 ToB 版的差别

`agents/desktop` 是桌面版(ToC)的能力包, 由 `agents/feishu` 抽取而来, **不含**飞书那套
工具。ToB 版的 `TOOLS.md` 在这行以下还有约 580 行飞书用法(群聊上下文、权限总原则、
授权引导、知识库写入 …), 它们逐条点名 `feishu_*` 工具 —— 这些工具在本能力包里不存在,
所以那些段落**不能**照抄过来: 提示词里点名一个不存在的工具, 模型会去调它然后拿到
工具不存在的错误, 属于净负担。

要给桌面版补本机专属的备忘(SSH、常用路径、API base URL), 就写在上面那半部分。
