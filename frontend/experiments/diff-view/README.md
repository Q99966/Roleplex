# G2 diff 展示候选试验

状态：历史选型试验。产品已采用 `react-diff-view`，当前实现见 [WriteDiff](../../src/components/WriteDiff.tsx)，交互与预算以[工具详情协议](../../../docs/protocol/public/messaging/tool-details.md)为准。该试验不再是开发前必须完成的选型关口。

不是产品组件，不由普通 build/E2E 导入。只使用生成的占位 patch，不连接后端、不读取用户源文件。

在本目录独立安装候选后运行（会从 npm 下载包，不调用模型）：

```bash
npm install --ignore-scripts --no-audit --no-fund
../../node_modules/.bin/tsc -p tsconfig.json
npm run build
npm run verify
```

需要先具备项目正常 frontend 的 Playwright/Chromium 环境。试验只输出体积、耗时和安全断言，生成的 JS/CSS/截图
已忽略，不修改产品 package.json 或锁文件。直接依赖固定版本，重跑仍应核对传递依赖；试验目录的完整依赖集合不等于产品依赖集合。
时间仅为本机单轮测量，不代表产品性能保证。此试验包含超过计划展示行数的压力样本，不能拿来绕过产品预算。

许可、实测结果、解析器适配限制与未覆盖项见 [G 验证记录](../../../docs/testing/tool-execution-g.md)。
