# 发布 / 安装到别的电脑

## 一、推到 GitHub（在能访问 GitHub 的机器上做一次）

```bash
cd dsh-pet-venti
git remote add origin https://github.com/CrazyHao/dsh-pet-venti.git
git branch -M main
git push -u origin main
```

> 这个目录里已经有一个本地仓库和提交（`git log` 可见）。
> 如果手上只有 `.bundle`：`git clone dsh-pet-venti.bundle dsh-pet-venti` 就能还原，再按上面推送。

推之前把 `package.json` 里的 `<your-name>` 换成你的 GitHub 用户名（repository / homepage / bugs 三处）。

## 二、别的电脑怎么装

1. 先装 **DSH** 和 **dsh-pet**，并让 dsh-pet 正常工作（桌宠能显示）。
2. DSH → 设置 → 插件 → **添加插件**，填 Git 地址：

   ```
   https://github.com/CrazyHao/dsh-pet-venti
   ```

   （也可以填本地目录、tarball，或先 clone 再选目录）
3. 装完点 **启用**。
4. 装运行环境（约 20 GB，一次性）：

   ```powershell
   cd <插件目录>
   powershell -ExecutionPolicy Bypass -File .\setup.ps1
   ```

   想换位置：`-DataDir D:\dsh-pet-venti`；先不下模型：`-SkipModels`。
5. 放参考音频到 `%USERPROFILE%\.dsh\pet-venti\voices\`（5~15 秒、单人、无 BGM）。
6. 重启 DSH（或停用再启用插件）。

验证：桌宠弹一句吐槽、脚下出现「正在观看：xxx」；双击插件目录里的 `watch.cmd` 可以实时看它在看什么。

## 三、备注

- 插件不含任何参考音频和模型，`setup.ps1` 会把模型下到 `%USERPROFILE%\.dsh\pet-venti\checkpoints`。
- 装到别的目录后，`paths.json` 会写到 `%USERPROFILE%\.dsh\pet-venti\paths.json`，插件升级不会丢。
- 只有一张 NVIDIA 卡时，游戏和 TTS 会抢显存；显存低于 1.2 GB 空闲时插件自动只弹气泡不朗读。
