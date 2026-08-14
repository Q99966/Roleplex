import { useEffect, useState } from 'react'
import { WandSparkles, ArrowRight, Bot, Cpu, Users, CheckCircle2, ChevronLeft, ChevronRight } from 'lucide-react'

interface LandingPageProps {
  onEnterWorkbench: () => void
}

interface ScenarioMessage {
  sender: string
  role: string
  text: string
  code?: string
  table?: { date: string; item: string; sales: string }[]
}

interface Scenario {
  task: string
  category: string
  agents: string[]
  messages: ScenarioMessage[]
}

/** 真实的群聊实战案例数据，包含真实对话流、代码块与数据表 */
const SCENARIOS: Scenario[] = [
  {
    task: "写一个带深色模式切换的 React 计数器，并进行单元测试",
    category: "Web 软件敏捷研发",
    agents: ["Orchestrator (主调度)", "代码专家", "审计专家"],
    messages: [
      { sender: "User", role: "Owner", text: "写一个带深色模式切换的 React 计数器，并进行单元测试。" },
      { sender: "Orchestrator", role: "协调者", text: "已收到。规划工作流：\n1. 代码专家编写 React 组件；\n2. 审计专家编写单元测试并执行验证。" },
      { sender: "代码专家", role: "Agent", text: "已完成 React 计数器组件，包含深色模式切换状态，代码实现如下：", code: `import React, { useState } from 'react';\n\nexport function Counter() {\n  const [count, setCount] = useState(0);\n  const [darkMode, setDarkMode] = useState(false);\n\n  return (\n    <div className={darkMode ? 'dark bg-slate-900 text-slate-100 p-6' : 'bg-white text-slate-900 p-6'}>\n      <p className="text-xl font-bold">Count: {count}</p>\n      <div className="flex gap-2 mt-4">\n        <button onClick={() => setCount(count + 1)} className="px-4 py-2 bg-indigo-600 text-white rounded">Increment</button>\n        <button onClick={() => setDarkMode(!darkMode)} className="px-4 py-2 bg-slate-500 text-white rounded">Toggle Theme</button>\n      </div>\n    </div>\n  );\n}` },
      { sender: "审计专家", role: "Agent", text: "已在单元测试沙箱中执行验证。测试结果：\n- 状态更新测试: PASS\n- 样式切换测试: PASS\n- 测试覆盖率: 100% (PASS)" }
    ]
  },
  {
    task: "抓取 sales_data.csv 过滤出销售额超过 10,000 的记录，并排版 Markdown 报告",
    category: "数据自动过滤与规整",
    agents: ["Orchestrator (主调度)", "数据提取", "文档顾问"],
    messages: [
      { sender: "User", role: "Owner", text: "提取销售额大于 10,000 的记录，并制作成报告。" },
      { sender: "Orchestrator", role: "协调者", text: "规划工作流：\n1. 数据提取 Agent 筛选销售额大于 10,000 的行；\n2. 文档顾问排版 Markdown 格式报表。" },
      { sender: "数据提取", role: "Agent", text: "已完成过滤，符合要求的销售记录如下：", table: [
        { date: "2026-08-12", item: "GPU 服务器", sales: "$12,500" },
        { date: "2026-08-13", item: "数据库托管", sales: "$10,800" }
      ] },
      { sender: "文档顾问", role: "Agent", text: "已根据过滤数据，排版生成 Markdown 格式的报告：\n\n### 销售额达标报告\n* **筛选标准**：单笔交易额 > $10,000\n* **总计交易笔数**：2 笔\n* **环比增长率**：+14.5%\n报告已同步写入暂存工作盘中。" }
    ]
  },
  {
    task: "翻译“保存并退出工作台，所有进度已保存”并进行软件语境校验",
    category: "多国语言本地化与语境审计",
    agents: ["Orchestrator (主调度)", "翻译专家", "审计专家"],
    messages: [
      { sender: "User", role: "Owner", text: "翻译“保存并退出工作台，所有进度已保存”成英文和日文，并进行语境校验。" },
      { sender: "Orchestrator", role: "协调者", text: "工作流规划：\n1. 翻译专家进行多语种直译与意译；\n2. 审计专家进行术语及软件语境一致性校验。" },
      { sender: "翻译专家", role: "Agent", text: "直译与意译结果如下：\n\n- **英文 (EN)**:\n  * Option 1: Save and exit workspace. All progress has been saved.\n  * Option 2: Save changes and quit workbench; all progress has been preserved.\n- **日文 (JA)**:\n  * 保存してワークスペースを終了します。すべての進捗が保存されました。" },
      { sender: "审计专家", role: "Agent", text: "语境校验意见：\n1. 英文 Option 2 中的 'workbench' (工作台) 与软件底层专有名词高度吻合，推荐在生产线使用。\n2. 日文译文的 '進捗' (进度) 语义地道，无突兀感。\n校验结果：APPROVED (通过)。" }
    ]
  }
]

/** Roleplex 平台官方产品介绍主页（搭载动态生产线与真实对话滑动展示区）。 */
export function LandingPage({ onEnterWorkbench }: LandingPageProps) {
  // 右侧 SVG 拓扑发光管线状态机
  const [activeStep, setActiveStep] = useState(0)
  // 下方真实对话滑动展示区
  const [currentSlide, setCurrentSlide] = useState(0)
  const [isHovered, setIsHovered] = useState(false)

  // 拓扑图状态推进
  useEffect(() => {
    const interval = setInterval(() => {
      setActiveStep((prev) => (prev + 1) % 7)
    }, 1500)
    return () => clearInterval(interval)
  }, [])

  // 滑动展示区自动轮播（当鼠标 Hover 在卡片上时暂停以供细读代码）
  useEffect(() => {
    if (isHovered) return
    const interval = setInterval(() => {
      setCurrentSlide((prev) => (prev + 1) % SCENARIOS.length)
    }, 5500)
    return () => clearInterval(interval)
  }, [isHovered])

  const nextSlide = () => {
    setCurrentSlide((prev) => (prev + 1) % SCENARIOS.length)
  }

  const prevSlide = () => {
    setCurrentSlide((prev) => (prev - 1 + SCENARIOS.length) % SCENARIOS.length)
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans select-none overflow-x-hidden relative flex flex-col justify-between">
      
      {/* GPU 加速管线与滑屏动画 CSS 样式 */}
      <style>{`
        @keyframes strokeFlowDown {
          from { stroke-dashoffset: 30; }
          to { stroke-dashoffset: 0; }
        }
        @keyframes strokeFlowUp {
          from { stroke-dashoffset: 0; }
          to { stroke-dashoffset: 30; }
        }
        @keyframes strokeFlowRight {
          from { stroke-dashoffset: 30; }
          to { stroke-dashoffset: 0; }
        }
        .flow-line-static {
          stroke: #111827;
          stroke-width: 1.5;
          stroke-linecap: round;
        }
        .flow-line-tool-static {
          stroke: #111827;
          stroke-width: 1.5;
          stroke-dasharray: 4 4;
          stroke-linecap: round;
        }
        .flow-line-active-down {
          stroke: url(#cyanPurpleGrad);
          stroke-width: 2.5;
          stroke-linecap: round;
          stroke-dasharray: 6 12;
          animation: strokeFlowDown 0.8s linear infinite;
        }
        .flow-line-active-up {
          stroke: url(#cyanPurpleGrad);
          stroke-width: 2.5;
          stroke-linecap: round;
          stroke-dasharray: 6 12;
          animation: strokeFlowUp 0.8s linear infinite;
        }
        .flow-line-active-right {
          stroke: #818cf8;
          stroke-width: 2.5;
          stroke-linecap: round;
          stroke-dasharray: 6 12;
          animation: strokeFlowRight 0.8s linear infinite;
        }
        .flow-line-tool-active-down {
          stroke: #10b981;
          stroke-width: 2.0;
          stroke-linecap: round;
          stroke-dasharray: 4 8;
          animation: strokeFlowDown 0.8s linear infinite;
        }
        .flow-line-tool-active-up {
          stroke: #10b981;
          stroke-width: 2.0;
          stroke-linecap: round;
          stroke-dasharray: 4 8;
          animation: strokeFlowUp 0.8s linear infinite;
        }
      `}</style>

      {/* 背景氛围发光斑 */}
      <div className="absolute top-0 left-1/4 w-[500px] h-[500px] rounded-full bg-indigo-500/5 blur-[150px] pointer-events-none z-0"></div>
      <div className="absolute bottom-10 right-1/4 w-[500px] h-[500px] rounded-full bg-cyan-500/5 blur-[150px] pointer-events-none z-0"></div>

      {/* 头部导航栏 */}
      <header className="border-b border-slate-900 bg-slate-950/80 backdrop-blur-md h-16 shrink-0 px-6 md:px-12 flex items-center justify-between relative z-10">
        <div className="flex items-center gap-2.5">
          <div className="rounded-lg bg-indigo-600 p-2 text-white shadow-md shadow-indigo-600/30">
            <WandSparkles size={18} />
          </div>
          <span className="font-bold text-white text-lg tracking-wide bg-gradient-to-r from-white to-slate-300 bg-clip-text text-transparent">Roleplex</span>
        </div>
        
        <button
          type="button"
          onClick={onEnterWorkbench}
          className="rounded-xl bg-indigo-600 px-5 py-2 text-xs font-semibold text-white hover:bg-indigo-500 transition shadow-lg shadow-indigo-600/20 active:scale-[0.98]"
        >
          进入工作台
        </button>
      </header>

      {/* 主体区 */}
      <main className="flex-1 flex flex-col items-center px-6 py-12 md:py-20 max-w-6xl w-full mx-auto relative z-10 space-y-20 md:space-y-28">
        
        {/* Hero Section */}
        <section className="grid grid-cols-1 md:grid-cols-12 gap-8 md:gap-12 w-full items-center">
          {/* 左侧：介绍 */}
          <div className="md:col-span-6 space-y-6 text-left">
            <div className="inline-flex items-center gap-2 rounded-full border border-indigo-500/20 bg-indigo-950/30 px-3 py-1 text-xs text-indigo-400 font-semibold tracking-wide">
              <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-ping"></span>
              <span>Milestone M1 · 已就绪</span>
            </div>
            
            <h2 className="text-4xl md:text-5xl font-extrabold text-white tracking-tight leading-tight">
              多智能体协同，<br />
              <span className="bg-gradient-to-r from-indigo-400 via-indigo-200 to-cyan-400 bg-clip-text text-transparent">重新定义群聊。</span>
            </h2>
            
            <p className="text-slate-400 text-sm md:text-base leading-relaxed max-w-lg">
              Roleplex 是一款个人多 Agent 协同群聊工作台。在这里，您可以托管自己的大模型 API 密钥，定制不同职责的 Agent 实例，并拉起群聊让它们协同运作。
            </p>

            <div className="pt-4">
              <button 
                type="button" 
                onClick={onEnterWorkbench}
                className="inline-flex items-center gap-2.5 rounded-xl bg-indigo-600 px-7 py-3.5 font-semibold text-white hover:bg-indigo-500 transition shadow-xl shadow-indigo-600/30 active:scale-[0.98]"
              >
                <span>立即进入工作台</span>
                <ArrowRight size={16} />
              </button>
            </div>
          </div>

          {/* 右侧：多智能体协作与工具调用跑马灯运行图 */}
          <div className="md:col-span-6 w-full">
            <div className="bg-slate-900/60 border border-slate-850 p-6 rounded-3xl backdrop-blur-md shadow-2xl relative overflow-hidden">
              <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-855">
                <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">协作与工具网络拓扑 (Agentic Workflow & Tool Network)</span>
                <span className="text-[10px] bg-indigo-950 border border-indigo-900/60 rounded px-1.5 py-0.5 text-indigo-400 font-mono flex items-center gap-1">
                  <span className="w-1 h-1 rounded-full bg-cyan-400 animate-ping"></span>
                  <span>ACTIVE_WORKFLOW</span>
                </span>
              </div>

              {/* 节点容器（高度 350px） */}
              <div className="h-[350px] relative text-xs mt-4">
                
                {/* 拓扑管线图绘制 */}
                <svg className="absolute inset-0 w-full h-full pointer-events-none z-0">
                  <defs>
                    <linearGradient id="cyanPurpleGrad" x1="0%" y1="0%" x2="0%" y2="100%">
                      <stop offset="0%" stopColor="#06b6d4" />
                      <stop offset="100%" stopColor="#6366f1" />
                    </linearGradient>
                  </defs>

                  {/* 1. 用户 -> 调度中枢 */}
                  <line x1="50%" y1="36" x2="50%" y2="66" className="flow-line-static" />
                  {activeStep === 1 && (
                    <line x1="50%" y1="36" x2="50%" y2="66" className="flow-line-active-down" />
                  )}
                  {activeStep === 6 && (
                    <line x1="50%" y1="36" x2="50%" y2="66" className="flow-line-active-up" />
                  )}

                  {/* 2. 调度中枢 -> 代码专家 */}
                  <line x1="50%" y1="106" x2="25%" y2="156" className="flow-line-static" />
                  {activeStep === 2 && (
                    <line x1="50%" y1="106" x2="25%" y2="156" className="flow-line-active-down" />
                  )}
                  {activeStep === 6 && (
                    <line x1="50%" y1="106" x2="25%" y2="156" className="flow-line-active-up" />
                  )}

                  {/* 3. 调度中枢 -> 审计专家 */}
                  <line x1="50%" y1="106" x2="75%" y2="156" className="flow-line-static" />
                  {activeStep === 6 && (
                    <line x1="50%" y1="106" x2="75%" y2="156" className="flow-line-active-up" />
                  )}

                  {/* 4. 代码专家 <-> 审计专家（横向协同） */}
                  <line x1="25%" y1="176" x2="75%" y2="176" className="flow-line-static" />
                  {activeStep === 4 && (
                    <line x1="25%" y1="176" x2="75%" y2="176" className="flow-line-active-right" />
                  )}

                  {/* 5. 代码专家 -> 文件系统工具 */}
                  <line x1="25%" y1="196" x2="12%" y2="266" className="flow-line-tool-static" />
                  {activeStep === 2 && (
                    <line x1="25%" y1="196" x2="12%" y2="266" className="flow-line-tool-active-down" />
                  )}
                  {activeStep === 3 && (
                    <line x1="25%" y1="196" x2="12%" y2="266" className="flow-line-tool-active-up" />
                  )}

                  {/* 6. 代码专家/审计专家 -> 搜索引擎工具 */}
                  <line x1="25%" y1="196" x2="50%" y2="266" className="flow-line-tool-static" />
                  <line x1="75%" y1="196" x2="50%" y2="266" className="flow-line-tool-static" />
                  {activeStep === 4 && (
                    <line x1="75%" y1="196" x2="50%" y2="266" className="flow-line-tool-active-down" />
                  )}

                  {/* 7. 审计专家 -> 测试沙箱工具 */}
                  <line x1="75%" y1="196" x2="88%" y2="266" className="flow-line-tool-static" />
                  {activeStep === 5 && (
                    <line x1="75%" y1="196" x2="88%" y2="266" className="flow-line-tool-active-down" />
                  )}
                </svg>

                {/* 节点 1：用户指令 */}
                <div 
                  className={`absolute left-1/2 -translate-x-1/2 top-0 z-10 transition-all duration-500 rounded-xl px-4 py-2 border font-semibold shadow-md ${
                    activeStep === 0 
                      ? 'bg-slate-900 border-indigo-500 text-indigo-300 scale-105 shadow-[0_0_15px_rgba(99,102,241,0.3)]' 
                      : 'bg-slate-950 border-slate-800/80 text-slate-400'
                  }`}
                >
                  用户指令 (User Input)
                </div>

                {/* 节点 2：协调者 (Orchestrator) */}
                <div 
                  className={`absolute left-1/2 -translate-x-1/2 top-[66px] z-10 transition-all duration-500 rounded-xl px-4 py-2 border font-bold shadow-lg flex flex-col items-center justify-center min-w-[210px] ${
                    activeStep === 1 || activeStep === 6
                      ? 'bg-slate-900 border-cyan-500 text-cyan-300 scale-105 shadow-[0_0_15px_rgba(6,182,212,0.3)]' 
                      : 'bg-slate-950 border-slate-800/80 text-slate-400'
                  }`}
                >
                  <div className="flex items-center gap-1.5">
                    <span className={`w-1.5 h-1.5 rounded-full bg-cyan-400 ${activeStep === 1 ? 'animate-ping' : ''}`}></span>
                    <span>协调者 Agent (Orchestrator)</span>
                  </div>
                  <span className="text-[9px] text-slate-500 font-mono mt-0.5">
                    {activeStep === 1 ? '[PLANNING_WORKFLOW]' : activeStep === 6 ? '[COMPILING_RESPONSE]' : '[IDLE_STANDBY]'}
                  </span>
                </div>

                {/* 节点 3：代码专家 (Sub-Agent Left) */}
                <div 
                  className={`absolute left-[5%] top-[148px] w-[40%] z-10 transition-all duration-500 rounded-xl p-3 border font-semibold shadow-md ${
                    activeStep === 2 || activeStep === 3
                      ? 'bg-slate-900 border-indigo-500 text-indigo-300 scale-105 shadow-[0_0_15px_rgba(99,102,241,0.25)]' 
                      : activeStep === 4
                        ? 'bg-slate-950 border-indigo-900/60 text-indigo-400/80'
                        : 'bg-slate-950 border-slate-800/80 text-slate-500'
                  }`}
                >
                  <p className="font-bold text-center">代码专家</p>
                  <span className="text-[9px] text-slate-500 block text-center mt-1 font-mono">
                    {activeStep === 2 ? '[CALLING_TOOL_FILE]' : activeStep === 3 ? '[COMPILING_CODE]' : activeStep === 4 ? '[COLLABORATING]' : '[STANDBY]'}
                  </span>
                </div>

                {/* 节点 4：审计专家 (Sub-Agent Right) */}
                <div 
                  className={`absolute right-[5%] top-[148px] w-[40%] z-10 transition-all duration-500 rounded-xl p-3 border font-semibold shadow-md ${
                    activeStep === 4 || activeStep === 5
                      ? 'bg-slate-900 border-purple-500 text-purple-300 scale-105 shadow-[0_0_15px_rgba(168,85,247,0.25)]' 
                      : 'bg-slate-950 border-slate-800/80 text-slate-500'
                  }`}
                >
                  <p className="font-bold text-center">审计专家</p>
                  <span className="text-[9px] text-slate-500 block text-center mt-1 font-mono">
                    {activeStep === 4 ? '[SEARCHING_REGULATIONS]' : activeStep === 5 ? '[RUNNING_TESTS]' : '[STANDBY]'}
                  </span>
                </div>

                {/* 节点 5：文件系统工具 (Left Tool) */}
                <div 
                  className={`absolute left-[2%] top-[266px] w-[29%] z-10 transition-all duration-500 rounded-xl p-2.5 border border-dashed text-center ${
                    activeStep === 2 || activeStep === 3
                      ? 'bg-emerald-950/20 border-emerald-500 text-emerald-400 shadow-[0_0_12px_rgba(16,185,129,0.2)]'
                      : 'bg-slate-950/40 border-slate-900 text-slate-650'
                  }`}
                >
                  <p className="font-semibold text-[10px]">文件系统</p>
                  <span className="text-[8px] block font-mono text-slate-500 mt-0.5">
                    {activeStep === 2 ? '写入源码' : '就绪'}
                  </span>
                </div>

                {/* 节点 6：搜索引擎工具 (Center Tool) */}
                <div 
                  className={`absolute left-1/2 -translate-x-1/2 top-[266px] w-[29%] z-10 transition-all duration-500 rounded-xl p-2.5 border border-dashed text-center ${
                    activeStep === 4
                      ? 'bg-emerald-950/20 border-emerald-500 text-emerald-400 shadow-[0_0_12px_rgba(16,185,129,0.2)]'
                      : 'bg-slate-950/40 border-slate-900 text-slate-650'
                  }`}
                >
                  <p className="font-semibold text-[10px]">搜索引擎</p>
                  <span className="text-[8px] block font-mono text-slate-500 mt-0.5">
                    {activeStep === 4 ? '检索规范' : '就绪'}
                  </span>
                </div>

                {/* 节点 7：测试沙箱工具 (Right Tool) */}
                <div 
                  className={`absolute right-[2%] top-[266px] w-[29%] z-10 transition-all duration-500 rounded-xl p-2.5 border border-dashed text-center ${
                    activeStep === 5
                      ? 'bg-emerald-950/20 border-emerald-500 text-emerald-400 shadow-[0_0_12px_rgba(16,185,129,0.2)]'
                      : 'bg-slate-950/40 border-slate-900 text-slate-650'
                  }`}
                >
                  <p className="font-semibold text-[10px]">测试沙箱</p>
                  <span className="text-[8px] block font-mono text-slate-500 mt-0.5">
                    {activeStep === 5 ? '单元测试' : '就绪'}
                  </span>
                </div>

              </div>
            </div>
          </div>
        </section>

        {/* Sliding Case Showcase Section (真实对话滑动展示区) */}
        <section className="w-full space-y-8">
          <div className="text-center space-y-2">
            <div className="inline-flex items-center gap-1.5 rounded-full border border-cyan-500/20 bg-cyan-950/30 px-3 py-1 text-[11px] text-cyan-400 font-bold uppercase tracking-wider">
              <span>Interactive Simulator</span>
            </div>
            <h3 className="text-2xl md:text-3xl font-extrabold text-white tracking-tight">群聊协作实战演练</h3>
            <p className="text-slate-500 text-xs md:text-sm max-w-xl mx-auto leading-relaxed">
              以滑动幻灯片形式还原真实的群聊对话场景，看不同职责的 Agent 与外部工具如何在统一中控流下交接流转与共同解题。
            </p>
          </div>

          {/* 轮播框架 */}
          <div 
            className="w-full max-w-4xl mx-auto relative group"
            onMouseEnter={() => setIsHovered(true)}
            onMouseLeave={() => setIsHovered(false)}
          >
            {/* 上一张 / 下一张按钮 (绝对定位在两侧，大屏悬浮，小屏隐藏) */}
            <button 
              type="button" 
              onClick={prevSlide}
              className="absolute left-[-24px] md:left-[-54px] top-1/2 -translate-y-1/2 z-20 rounded-xl p-3 bg-slate-900/60 border border-slate-800 text-slate-400 hover:text-white hover:bg-slate-800 transition active:scale-95 shadow-2xl backdrop-blur-md opacity-0 group-hover:opacity-100 transition-opacity duration-300"
              aria-label="Previous Slide"
            >
              <ChevronLeft size={20} />
            </button>
            <button 
              type="button" 
              onClick={nextSlide}
              className="absolute right-[-24px] md:right-[-54px] top-1/2 -translate-y-1/2 z-20 rounded-xl p-3 bg-slate-900/60 border border-slate-800 text-slate-400 hover:text-white hover:bg-slate-800 transition active:scale-95 shadow-2xl backdrop-blur-md opacity-0 group-hover:opacity-100 transition-opacity duration-300"
              aria-label="Next Slide"
            >
              <ChevronRight size={20} />
            </button>

            {/* 卡片容器：包含滑动视口 */}
            <div className="w-full overflow-hidden rounded-3xl border border-slate-850 bg-slate-900/40 backdrop-blur-xl shadow-2xl relative min-h-[460px] flex flex-col">
              
              {/* 卡片顶端状态条 */}
              <div className="border-b border-slate-850 px-6 py-4 flex flex-wrap items-center justify-between gap-3 bg-slate-950/20 shrink-0">
                <div className="flex items-center gap-3">
                  <span className="text-xs font-bold text-slate-400 uppercase tracking-widest font-mono">CASE 0{currentSlide + 1} / 03</span>
                  <span className="text-[10px] px-2 py-0.5 rounded bg-indigo-950 text-indigo-400 font-semibold border border-indigo-900/60">
                    {SCENARIOS[currentSlide].category}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-slate-500">协同角色:</span>
                  <div className="flex items-center gap-1.5">
                    {SCENARIOS[currentSlide].agents.map((agent, i) => (
                      <span key={i} className="text-[10px] px-2 py-0.5 rounded bg-slate-950 border border-slate-850 text-slate-300 font-semibold">
                        {agent}
                      </span>
                    ))}
                  </div>
                </div>
              </div>

              {/* 卡片核心区：任务及对话流展示 */}
              <div className="p-6 md:p-8 flex-1 flex flex-col justify-between space-y-6">
                
                {/* 顶部现实任务要求 */}
                <div className="bg-slate-950/60 border border-slate-850 rounded-2xl p-4 flex items-start gap-3 relative overflow-hidden">
                  <div className="absolute top-0 right-0 w-24 h-24 bg-indigo-500/5 rounded-full blur-xl pointer-events-none"></div>
                  <div className="rounded-lg bg-indigo-600/10 border border-indigo-500/20 p-2 text-indigo-400 shrink-0 text-xs font-mono font-bold">
                    TASK
                  </div>
                  <div className="space-y-1">
                    <p className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold font-mono">任务目标 (Active Requirement)</p>
                    <p className="text-xs md:text-sm font-semibold text-slate-200">
                      “{SCENARIOS[currentSlide].task}”
                    </p>
                  </div>
                </div>

                {/* 模拟群聊对话流 */}
                <div className="space-y-4 flex-1">
                  <p className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold font-mono mb-2">协作会话流程 (Agent chat stream)</p>
                  <div className="space-y-3.5 max-h-[320px] overflow-y-auto pr-1">
                    {SCENARIOS[currentSlide].messages.map((msg, index) => {
                      const isUser = msg.role === 'Owner'
                      const isOrch = msg.role === '协调者'
                      
                      return (
                        <div key={index} className="flex flex-col space-y-1 text-xs">
                          {/* 发送人昵称与角色标签 */}
                          <div className="flex items-center gap-1.5 px-1">
                            <span className="font-bold text-slate-300">{msg.sender}</span>
                            <span className={`text-[8px] px-1 rounded-sm uppercase tracking-wide font-bold scale-90 ${
                              isUser ? 'bg-indigo-950 text-indigo-400 border border-indigo-900/60' :
                              isOrch ? 'bg-cyan-950 text-cyan-400 border border-cyan-900/60' :
                              'bg-slate-950 text-slate-500 border border-slate-850'
                            }`}>
                              {msg.role}
                            </span>
                          </div>

                          {/* 对话气泡 */}
                          <div className={`rounded-xl p-3 max-w-[96%] leading-relaxed ${
                            isUser ? 'bg-slate-950 border border-slate-850 text-slate-200' :
                            isOrch ? 'bg-cyan-950/20 border border-cyan-900/30 text-cyan-200/90' :
                            'bg-slate-900/80 border border-slate-850/80 text-slate-300'
                          }`}>
                            <p className="whitespace-pre-line text-[11px] md:text-xs">{msg.text}</p>
                            
                            {/* 如果消息中包含代码段，则高能渲染出来 */}
                            {msg.code && (
                              <pre className="mt-3 rounded-lg bg-slate-950 border border-slate-850 p-3 font-mono text-[9px] md:text-[10px] text-indigo-300 overflow-x-auto max-w-full whitespace-pre leading-relaxed select-text">
                                <code>{msg.code}</code>
                              </pre>
                            )}

                            {/* 如果消息中包含表格（数据提取案例） */}
                            {msg.table && (
                              <div className="mt-3 overflow-x-auto rounded-lg border border-slate-850 select-text">
                                <table className="w-full text-[10px] text-left border-collapse bg-slate-950">
                                  <thead>
                                    <tr className="border-b border-slate-850 text-slate-400 font-semibold">
                                      <th className="px-3 py-1.5">日期</th>
                                      <th className="px-3 py-1.5">商品</th>
                                      <th className="px-3 py-1.5 text-right">销售额</th>
                                    </tr>
                                  </thead>
                                  <tbody className="divide-y divide-slate-900 text-slate-300">
                                    {msg.table.map((row, rid) => (
                                      <tr key={rid}>
                                        <td className="px-3 py-1.5 font-mono">{row.date}</td>
                                        <td className="px-3 py-1.5">{row.item}</td>
                                        <td className="px-3 py-1.5 text-right font-mono text-emerald-400">{row.sales}</td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>

              </div>

              {/* 轮播底端导航点与操作区 */}
              <div className="border-t border-slate-850 px-6 py-4 flex items-center justify-between bg-slate-950/20 shrink-0">
                <span className="text-[9px] text-slate-500 font-mono">
                  {isHovered ? '已暂停自动轮播（鼠标悬停）' : '自动轮播中（每 5.5 秒切换）'}
                </span>
                
                {/* 圆点指示器 */}
                <div className="flex items-center gap-2">
                  {SCENARIOS.map((_, i) => (
                    <button
                      key={i}
                      type="button"
                      onClick={() => setCurrentSlide(i)}
                      className={`h-2 rounded-full transition-all duration-300 ${
                        currentSlide === i ? 'w-6 bg-indigo-500' : 'w-2 bg-slate-800 hover:bg-slate-700'
                      }`}
                      aria-label={`Go to slide ${i + 1}`}
                    />
                  ))}
                </div>

                <div className="flex items-center gap-1.5">
                  <button 
                    type="button"
                    onClick={prevSlide}
                    className="p-1 rounded bg-slate-950 border border-slate-850 text-slate-400 hover:text-white hover:bg-slate-800 transition"
                    aria-label="Prev Case"
                  >
                    <ChevronLeft size={14} />
                  </button>
                  <button 
                    type="button"
                    onClick={nextSlide}
                    className="p-1 rounded bg-slate-950 border border-slate-850 text-slate-400 hover:text-white hover:bg-slate-800 transition"
                    aria-label="Next Case"
                  >
                    <ChevronRight size={14} />
                  </button>
                </div>
              </div>

            </div>
          </div>
        </section>

        {/* Features Column Section */}
        <section className="w-full space-y-8">
          <div className="text-center space-y-2">
            <h3 className="text-xl md:text-2xl font-extrabold text-white tracking-tight">Roleplex 核心三大支柱</h3>
            <p className="text-slate-500 text-xs">通过三个极简步骤，拉起您的多 Agent 协作工作群</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 w-full">
            <div className="bg-slate-900/40 border border-slate-900 p-6 rounded-2xl flex flex-col justify-between">
              <div className="space-y-4">
                <div className="w-10 h-10 rounded-xl bg-slate-950 border border-slate-850 flex items-center justify-center text-indigo-400">
                  <Cpu size={20} />
                </div>
                <h4 className="font-bold text-slate-200">01 / 密钥隔离托管</h4>
                <p className="text-slate-500 text-xs leading-relaxed">
                  本地安全托管各大模型厂商（如 OpenAI、Anthropic、DeepSeek 等）的 API Key。数据加密并仅保存在本机构建的 SQLite 数据库，保护您的隐私。
                </p>
              </div>
              <ul className="mt-6 space-y-2 text-[11px] text-slate-400">
                <li className="flex items-center gap-1.5">
                  <CheckCircle2 size={12} className="text-indigo-400" />
                  <span>支持 OpenAI 兼容代理</span>
                </li>
                <li className="flex items-center gap-1.5">
                  <CheckCircle2 size={12} className="text-indigo-400" />
                  <span>本地数据库加密保护</span>
                </li>
              </ul>
            </div>

            <div className="bg-slate-900/40 border border-slate-900 p-6 rounded-2xl flex flex-col justify-between">
              <div className="space-y-4">
                <div className="w-10 h-10 rounded-xl bg-slate-950 border border-slate-850 flex items-center justify-center text-indigo-400">
                  <Bot size={20} />
                </div>
                <h4 className="font-bold text-slate-200">02 / 自由定制 Agent 角色</h4>
                <p className="text-slate-500 text-xs leading-relaxed">
                  通过自定义 System Prompt 提示词、温度参数及专属职责标签，创造定位不同的 AI 智能体，并为其指派专用的大模型密钥。
                </p>
              </div>
              <ul className="mt-6 space-y-2 text-[11px] text-slate-400">
                <li className="flex items-center gap-1.5">
                  <CheckCircle2 size={12} className="text-indigo-400" />
                  <span>内置搜索及 URL 抓取工具</span>
                </li>
                <li className="flex items-center gap-1.5">
                  <CheckCircle2 size={12} className="text-indigo-400" />
                  <span>支持灵活参数与标签筛选</span>
                </li>
              </ul>
            </div>

            <div className="bg-slate-900/40 border border-slate-900 p-6 rounded-2xl flex flex-col justify-between">
              <div className="space-y-4">
                <div className="w-10 h-10 rounded-xl bg-slate-950 border border-slate-850 flex items-center justify-center text-indigo-400">
                  <Users size={20} />
                </div>
                <h4 className="font-bold text-slate-200">03 / 多角色群发调度</h4>
                <p className="text-slate-500 text-xs leading-relaxed">
                  拉起多角色群聊。开启 **Orchestrator 发言调度器**，由指定的调度角色智能决定对话的顺次与发言权，规避 AI 角色刷屏与抢麦。
                </p>
              </div>
              <ul className="mt-6 space-y-2 text-[11px] text-slate-400">
                <li className="flex items-center gap-1.5">
                  <CheckCircle2 size={12} className="text-indigo-400" />
                  <span>智能决策发言顺次</span>
                </li>
                <li className="flex items-center gap-1.5">
                  <CheckCircle2 size={12} className="text-indigo-400" />
                  <span>单聊/群聊双模式自如切换</span>
                </li>
              </ul>
            </div>
          </div>
        </section>
      </main>

      {/* 底部版权栏 */}
      <footer className="border-t border-slate-900 py-6 px-6 text-center text-xs text-slate-600 relative z-10 bg-slate-950/60">
        <p>© 2026 Roleplex Workspaces. All rights reserved.</p>
      </footer>
    </div>
  )
}
