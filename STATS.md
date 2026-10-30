# 主页统计说明

统计直接取自 GitHub 官方 API，生成的图片由本账号的 GitHub Pages 托管。深色、浅色卡片根据浏览器主题自动切换。

## 更新与实时性

- 计划每 5 分钟运行一次，错开整点；也可以在[更新主页统计](https://github.com/vighzhen/vighzhen/actions/workflows/profile-stats.yml)中点击 **Run workflow** 手动触发。
- 每张卡片都标注最后成功取数时间，使用 UTC+8。
- GitHub 的任务排队、API 数据处理、Pages CDN 和 README 图片缓存都可能延迟展示，因此这是近实时统计，不保证秒级实时或严格 5 分钟内刷新。
- 如果取数或生成失败，之前成功发布的统计保持可见，更新时间不会伪装为最新。
- GitHub 可能在公共仓库连续 60 天没有活动时停用定时工作流；可在 Actions 页面重新启用。
- [完整统计页面](https://vighzhen.github.io/vighzhen/)会同时展示两张卡片和最后取数时间。

## 统计口径

| 指标 | 计算方式 |
| :--- | :--- |
| 公开仓库 | GitHub 用户 API 的 `public_repos`，并单独显示当前可访问的非 Fork 公开仓库数量。 |
| 仓库星标 | 账号名下非 Fork 公开仓库的 `stargazerCount` 总和。不是账号给其他仓库点过的星标。 |
| 关注者 | GitHub 用户 API 的 `followers`。 |
| 今年公开贡献 | 当年 1 月 1 日至取数时刻，GitHub GraphQL 贡献日历的 `totalContributions`。公开页面使用工作流 Token 读取的公开活动。 |
| 贡献明细 | 同一时间范围内 GitHub 的提交、PR、Issue 贡献计数。总贡献还包含其他类型活动，因此不要求明细之和等于总贡献。 |
| 代码语言 | 非 Fork 公开仓库的 GitHub 语言字节数总和，排除主页仓库本身；展示前五种语言，其余合并为“其他”。 |

贡献统计遵循 GitHub 的归属和可见性规则，以工作流访问身份可见的公开数据为准，不等同于所有分支的提交数，也不显示私有仓库名称或源码。你登录本人账号查看的贡献日历可能包含私有活动，因此可能比这里显示的公开贡献多。语言比例不代表熟练程度。二次开发项目若没有 GitHub Fork 标记，按非 Fork 项目计入。

所有仓库分页完整读取；语言比例使用完整字节数作为分母，不重新放大前五种语言的比例。GitHub API 若返回不完整的语言数据，本次更新会失败而不会发布误导结果。

## 维护

生成脚本：`scripts/build_stats.py`  
工作流：`.github/workflows/profile-stats.yml`

Python 脚本只用标准库，通过 GitHub CLI 读取 API。工作流使用仓库自带的短期 `GITHUB_TOKEN`，不需要另存个人 Token；结果直接部署到 Pages，不产生定时 Git 提交。

本地生成：

```bash
python3 scripts/build_stats.py --login vighzhen --output _site
```

结果包含四张 SVG、完整的 `stats.json` 和响应式统计页面。没有第三方统计卡片服务、外部字体或图片依赖。

本地使用个人账号生成时，卡片会标注“今年可见贡献”，因为该账号可能读取到私有活动；只有 GitHub Actions 中生成并发布的版本标注“今年公开贡献”。
