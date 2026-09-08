# 安装器升级路径下的用户数据存活 · 事实与待验清单

## 结论

**Windows 安装器升级时,`{app}\app` 是被整目录改名换新的,不是逐文件覆盖。** 用户在 `{app}\app` 下养出来的东西(`SOUL.md` / `USER.md` / `schedules/`)不会被"覆盖",而是被遗弃在 `{app}\app.backup` 里;而 `SwapComponent` 每次执行时会先 `DelTree` 掉上一次的 backup,据此推断**用户数据只能活过一次升级**。

这一条是**推断,未实测** —— 真装真升的实验尚未做,本文档第三节列了要验什么。

修复方向已经收敛到一条: **把用户数据移出被换掉的目录**,且落点要在 `{app}` 之外。具体落点是产品决定,未拍。

本文档只记事实与待验项,不含实施方案。前身是 Kanban 卡 3a79a / 33fe6,两卡因「本轮只做开发架构、不做打包部署」被推后,其研究结论搬到此处后卡已删除。

## 一、机制:整目录改名,不是覆盖

证据来自 `.github/inno-setup/haitun.iss` 静态阅读(含 `[Code]` 段)。

> **按符号引用,不按行号。** 3a79a / 33fe6 两卡记的行号(`:216` `:230` `:402` 等)在 2026-09-01 复核时已全部漂移约 +13 行(该文件现 463 行),机制本身逐条仍成立。所以此处一律用符号名与代码片段锚定。

调用链:

```
PrepareToInstall            在任何文件拷贝之前执行
  └─ SwapComponent('app')   把 {app}\app 改名为 {app}\app.backup
       └─ [Files] 段        之后重建一个全新的 {app}\app
```

`SwapComponent` 的实际行为(原文):

```pascal
function SwapComponent(const Name: String): Boolean;
begin
  Cur := ComponentDir(Name);
  Backup := Cur + '.backup';
  ...
  while i <= 5 do
  begin
    if DirExists(Backup) then
      DelTree(Backup, True, True, True);   ← 删掉上一次升级留下的 backup
  ...
  Result := RenameFile(Cur, Backup);       ← 整目录改名,不是逐文件操作
```

三条推论:

1. **`ignoreversion` 在升级路径上根本没起作用。** 该标志治的是"文件还在原地时的覆盖行为";改名之后原地没有文件,`[Files]` 段是往一个空目录里全新写入。
2. **`SOUL.md` / `USER.md` 不是被覆盖,是被遗弃在 `{app}\app.backup` 里。**
3. **用户数据只能活过一次升级** —— 因为下一次 `SwapComponent` 进来就先 `DelTree` 掉这一次的 backup。**这条是推断,是本文档最需要实测的一条。**

## 二、两条修复路线,(a) 已被推翻

- **(a) 逐条加 `onlyifdoesntexist` —— 无效。** 该标志只防"文件还在原地时被覆盖";改名之后原地没有文件,被保护的文件照样重新写入。**这是改名问题,不是覆盖问题。**
- **(b) 把用户数据移出被换掉的目录 —— 唯一可行。** 已有先例: `rollback-state.json` 能活下来,靠的是它在 `{app}\` 而不在 `{app}\app`,与 `onlyifdoesntexist` 无关(它确实带了这个标志,但那不是它存活的原因):

  ```
  Source: "rollback-state.json"; DestDir: "{app}"; Flags: onlyifdoesntexist
  ```

**落点还要再往外一层。** `[UninstallDelete]` 会连 `{app}` 一起删:

```
[UninstallDelete]
Type: filesandordirs; Name: "{app}\*"
Type: filesandordirs; Name: "{app}"
```

所以挪到 `{app}\userdata` 在卸载重装时仍会丢。**真正的归宿在 `{app}` 之外。** 具体落点是产品决定,未拍。

## 三、待验清单(只出证据,不改代码、不改 .iss)

要求:逐条标明证据是**实测**还是**静态**。不要用静态阅读冒充实测 —— 此前已经犯过一次这个错。

1. 真装一次 → 手工改 `SOUL.md` / `USER.md`、在 `schedules/` 下留可辨识文件 → 真升一次 → 报告这些文件去哪了(是否出现在 `{app}\app.backup`、新的 `{app}\app` 里是什么内容)。给哈希或内容。
2. **最重要的一条**: 再升第二次,确认 `SwapComponent` 里的 `DelTree` 是否真的删掉了第一次的 `app.backup`。「用户数据只能活过一次升级」目前是推断,这条是唯一的验证途径。
3. 顺带确认 `FreshInstall` 判定、`SwapComponent` 失败回退路径、`CurStepChanged` 的 `ssPostInstall` 分支在真实升级里的实际走向。
4. 做不了真实安装升级(缺 Inno Setup 编译器、缺签名、需管理员权限等)就**明确说清卡在哪、缺什么**。

产出形式:结论先行的报告 —— `SOUL.md` / `USER.md` / `schedules/` 在一次升级、两次升级后分别是什么状态。

## 四、明确超出范围、需单独决定的两件

- **存量用户的数据迁移。** 改了读取位置后,数据仍在 `{app}\app` 的用户会看起来丢东西,除非写迁移。负责人称「还没人真的在用这个能力」,但已装机用户的实际情况需要核实。
- **卸载语义**: 卸载该不该删掉用户养出来的海豚。这是产品甚至法务决定。
