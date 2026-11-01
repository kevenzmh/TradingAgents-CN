/**
 * 高级课程内容静态导入映射（社区版 · 全量 30 课）
 *
 * 课程正文通过 vite 的 ?raw 静态导入打包进前端；社区版课程全量免费
 * 开放（原发布管道只含前 5 节的截断策略已弃用）。
 */

// 全量课程文件导入映射（按阶段组织）
const courseContentMap: Record<string, () => Promise<{ default: string }>> = {
  // 阶段1：发现
  'phase-01-discover/lesson-01-stock-screening.md': () => import('../../../docs/07-courses/v3.0/phase-01-discover/lesson-01-stock-screening.md?raw'),
  'phase-01-discover/lesson-02-batch-analysis.md': () => import('../../../docs/07-courses/v3.0/phase-01-discover/lesson-02-batch-analysis.md?raw'),
  'phase-01-discover/lesson-03-single-analysis-basic.md': () => import('../../../docs/07-courses/v3.0/phase-01-discover/lesson-03-single-analysis-basic.md?raw'),
  'phase-01-discover/lesson-04-single-analysis-multi-scenario.md': () => import('../../../docs/07-courses/v3.0/phase-01-discover/lesson-04-single-analysis-multi-scenario.md?raw'),
  'phase-01-discover/lesson-05-discover-pitfalls.md': () => import('../../../docs/07-courses/v3.0/phase-01-discover/lesson-05-discover-pitfalls.md?raw'),
  // 阶段2：跟踪
  'phase-02-track/lesson-06-assistant-overview.md': () => import('../../../docs/07-courses/v3.0/phase-02-track/lesson-06-assistant-overview.md?raw'),
  'phase-02-track/lesson-07-industry-research.md': () => import('../../../docs/07-courses/v3.0/phase-02-track/lesson-07-industry-research.md?raw'),
  'phase-02-track/lesson-08-daily-routine.md': () => import('../../../docs/07-courses/v3.0/phase-02-track/lesson-08-daily-routine.md?raw'),
  'phase-02-track/lesson-09-scheduled-analysis.md': () => import('../../../docs/07-courses/v3.0/phase-02-track/lesson-09-scheduled-analysis.md?raw'),
  // 阶段3：持仓
  'phase-03-position/lesson-10-paper-trading.md': () => import('../../../docs/07-courses/v3.0/phase-03-position/lesson-10-paper-trading.md?raw'),
  'phase-03-position/lesson-11-position-research.md': () => import('../../../docs/07-courses/v3.0/phase-03-position/lesson-11-position-research.md?raw'),
  'phase-03-position/lesson-12-position-sizing.md': () => import('../../../docs/07-courses/v3.0/phase-03-position/lesson-12-position-sizing.md?raw'),
  'phase-03-position/lesson-13-risk-tracking.md': () => import('../../../docs/07-courses/v3.0/phase-03-position/lesson-13-risk-tracking.md?raw'),
  'phase-03-position/lesson-14-position-pitfalls.md': () => import('../../../docs/07-courses/v3.0/phase-03-position/lesson-14-position-pitfalls.md?raw'),
  'phase-03-position/lesson-15-assistant-in-position.md': () => import('../../../docs/07-courses/v3.0/phase-03-position/lesson-15-assistant-in-position.md?raw'),
  // 阶段4：复盘
  'phase-04-review/lesson-16-trade-review.md': () => import('../../../docs/07-courses/v3.0/phase-04-review/lesson-16-trade-review.md?raw'),
  'phase-04-review/lesson-17-case-library.md': () => import('../../../docs/07-courses/v3.0/phase-04-review/lesson-17-case-library.md?raw'),
  'phase-04-review/lesson-18-settlement-alpha.md': () => import('../../../docs/07-courses/v3.0/phase-04-review/lesson-18-settlement-alpha.md?raw'),
  'phase-04-review/lesson-19-grid-backtest.md': () => import('../../../docs/07-courses/v3.0/phase-04-review/lesson-19-grid-backtest.md?raw'),
  // 阶段5：短线
  'phase-05-short-term/lesson-20-short-term-toolchain.md': () => import('../../../docs/07-courses/v3.0/phase-05-short-term/lesson-20-short-term-toolchain.md?raw'),
  'phase-05-short-term/lesson-21-short-term-playbook.md': () => import('../../../docs/07-courses/v3.0/phase-05-short-term/lesson-21-short-term-playbook.md?raw'),
  // 阶段6：中长线
  'phase-06-long-term/lesson-22-long-term-toolchain.md': () => import('../../../docs/07-courses/v3.0/phase-06-long-term/lesson-22-long-term-toolchain.md?raw'),
  'phase-06-long-term/lesson-23-long-term-playbook.md': () => import('../../../docs/07-courses/v3.0/phase-06-long-term/lesson-23-long-term-playbook.md?raw'),
  // 阶段7：扩展
  'phase-07-extension/lesson-24-workflow-architect.md': () => import('../../../docs/07-courses/v3.0/phase-07-extension/lesson-24-workflow-architect.md?raw'),
  'phase-07-extension/lesson-25-skill-center.md': () => import('../../../docs/07-courses/v3.0/phase-07-extension/lesson-25-skill-center.md?raw'),
  // 阶段8：个人计划
  'phase-08-personal-plan/lesson-26-signal-center.md': () => import('../../../docs/07-courses/v3.0/phase-08-personal-plan/lesson-26-signal-center.md?raw'),
  'phase-08-personal-plan/lesson-27-backtest-methodology.md': () => import('../../../docs/07-courses/v3.0/phase-08-personal-plan/lesson-27-backtest-methodology.md?raw'),
  'phase-08-personal-plan/lesson-28-decision-log.md': () => import('../../../docs/07-courses/v3.0/phase-08-personal-plan/lesson-28-decision-log.md?raw'),
  'phase-08-personal-plan/lesson-29-personal-plan.md': () => import('../../../docs/07-courses/v3.0/phase-08-personal-plan/lesson-29-personal-plan.md?raw'),
  'phase-08-personal-plan/lesson-30-continuous-growth.md': () => import('../../../docs/07-courses/v3.0/phase-08-personal-plan/lesson-30-continuous-growth.md?raw'),
}

/**
 * 获取课程内容
 * @param filename 课程文件名（例如：'phase-01-discover/lesson-01-stock-screening.md'）
 * @returns Promise<string> 课程Markdown内容
 */
export async function getCourseContent(filename: string): Promise<string> {
  const loader = courseContentMap[filename]
  if (!loader) {
    throw new Error(`课程文件未找到: ${filename}`)
  }

  try {
    const module = await loader()
    return module.default
  } catch (error: any) {
    console.error(`加载课程文件失败: ${filename}`, error)
    throw new Error(`加载课程文件失败: ${filename} - ${error.message}`)
  }
}

/**
 * 同步获取课程内容（用于预加载）
 * 注意：这需要预先导入所有文件
 */
export function getCourseContentSync(_filename: string): string | null {
  return null
}
