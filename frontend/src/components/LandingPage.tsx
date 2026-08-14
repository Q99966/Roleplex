import { WandSparkles, ArrowRight, Bot, Cpu, Users, CheckCircle2 } from 'lucide-react'

interface LandingPageProps {
  onEnterWorkbench: () => void
}

/** Roleplex 平台官方产品介绍主页（极简高能暗色系设计）。 */
export function LandingPage({ onEnterWorkbench }: LandingPageProps) {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans select-none overflow-x-hidden relative flex flex-col justify-between">
      
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
          <div className="md:col-span-7 space-y-6 text-left">
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

          {/* 右侧：签名版中控流程示意卡片 */}
          <div className="md:col-span-5 w-full">
            <div className="bg-slate-900/60 border border-slate-850 p-6 rounded-3xl backdrop-blur-md shadow-2xl relative overflow-hidden">
              <div className="flex items-center justify-between mb-6 pb-4 border-b border-slate-800">
                <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">调度结构示意 (Orchestrator System)</span>
                <span className="text-[10px] bg-indigo-950 border border-indigo-900/60 rounded px-1.5 py-0.5 text-indigo-400 font-mono">FLOW_ACTIVE</span>
              </div>

              {/* 拓扑图表达 */}
              <div className="space-y-6 relative text-xs">
                {/* 输入节点 */}
                <div className="flex justify-center relative">
                  <div className="bg-slate-950 border border-slate-800 rounded-xl px-4 py-2 text-slate-300 font-semibold shadow-md relative z-10">
                    💡 用户指令 (User Input)
                  </div>
                  {/* 向下引导线 */}
                  <div className="absolute top-full left-1/2 -translate-x-1/2 w-0.5 h-6 bg-indigo-500/30"></div>
                </div>

                {/* 调度中枢 */}
                <div className="flex justify-center relative pt-2">
                  <div className="bg-indigo-950 border border-indigo-600/50 rounded-xl px-4 py-2.5 text-indigo-300 font-bold shadow-lg shadow-indigo-600/10 relative z-10 flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse"></span>
                    <span>协调者 Agent (Orchestrator)</span>
                  </div>
                  
                  {/* 分叉线 */}
                  <div className="absolute top-full left-1/2 -translate-x-1/2 w-48 h-6 border-x border-t border-indigo-500/20 mt-1"></div>
                  <div className="absolute top-full left-1/2 -translate-x-1/2 w-0.5 h-6 bg-indigo-500/20"></div>
                </div>

                {/* Agent 执行节点 */}
                <div className="grid grid-cols-3 gap-3 pt-6 relative">
                  <div className="bg-slate-950/80 border border-slate-850 rounded-xl p-3 text-center">
                    <p className="font-bold text-slate-200">代码专家</p>
                    <span className="text-[9px] text-slate-500 block mt-1">DeepSeek</span>
                  </div>
                  <div className="bg-slate-950/80 border border-slate-850 rounded-xl p-3 text-center">
                    <p className="font-bold text-slate-200">文档顾问</p>
                    <span className="text-[9px] text-slate-500 block mt-1">Claude 3.5</span>
                  </div>
                  <div className="bg-slate-950/80 border border-slate-850 rounded-xl p-3 text-center">
                    <p className="font-bold text-slate-200">语言翻译</p>
                    <span className="text-[9px] text-slate-500 block mt-1">GPT-4o</span>
                  </div>
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
                  拉起多角色群聊。开启 **Orchestrator 发言调度器**，由指定的调度角色智能决定对话的顺次和发言权，规避 AI 角色刷屏与抢麦。
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
