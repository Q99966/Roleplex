import { useEffect, useState } from 'react'
import { WandSparkles, ArrowRight, Bot, Cpu, Users, CheckCircle2 } from 'lucide-react'

interface LandingPageProps {
  onEnterWorkbench: () => void
}

/** Roleplex 平台官方产品介绍主页（搭载多 Agent 协同与工具调用动态生产线）。 */
export function LandingPage({ onEnterWorkbench }: LandingPageProps) {
  // 7 阶段循环生产线状态机，每 1.5 秒推进一个协作工序，完整循环 10.5 秒
  const [activeStep, setActiveStep] = useState(0)

  useEffect(() => {
    const interval = setInterval(() => {
      setActiveStep((prev) => (prev + 1) % 7)
    }, 1500)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans select-none overflow-x-hidden relative flex flex-col justify-between">
      
      {/* 自包含的 GPU 加速生产线动画 CSS 样式 */}
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
        /* 数据流动线样式 */
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
        /* 绿色工具流动线样式 */
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
      <main className="flex-1 flex flex-col items-center px-6 py-12 md:py-20 max-w-6xl w-full mx-auto relative z-10 space-y-16 md:space-y-24">
        
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
              <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-850">
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
                  拉起多角色群聊。开启 **Orchestrator 发言调度器**，由指定的调度角色智能决定对话的顺次 and 发言权，规避 AI 角色刷屏与抢麦。
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
