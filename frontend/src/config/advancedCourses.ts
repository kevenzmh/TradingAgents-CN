/**
 * 高级课程配置（社区版 · 全量 30 课）
 *
 * 社区版课程全量免费开放（原发布管道只保留前 5 节的截断策略已弃用）。
 * 课程内容位于 docs/07-courses/v3.0/phase-XX-name/，后端
 * advanced_courses 路由按文件名跨阶段目录定位。
 */

export interface Lesson {
  id: string
  title: string
  file: string  // Markdown文件路径（相对于 docs/07-courses/v3.0/）
  order: number
}

export interface CourseCategory {
  id: string
  name: string
  icon: string
  description: string
  lessonCount: number
  lessons: Lesson[]
}

/** 社区版全量开放 */
export const FREE_LESSON_COUNT = 30

/**
 * 判断课程是否免费
 */
export function isFreeLesson(order: number): boolean {
  return order <= FREE_LESSON_COUNT
}

/** 全量课程分类（30 课 · 8 阶段） */
const allCourseCategories: CourseCategory[] = [
  {
    id: 'phase-01-discover',
    name: '发现阶段',
    icon: '🔍',
    description: '从6000只股票里找到值得研究的',
    lessonCount: 5,
    lessons: [
      { id: 'lesson-01', title: '第1课：股票筛选（自然语言+条件筛选）', file: 'phase-01-discover/lesson-01-stock-screening.md', order: 1 },
      { id: 'lesson-02', title: '第2课：批量分析（单次≤10只）', file: 'phase-01-discover/lesson-02-batch-analysis.md', order: 2 },
      { id: 'lesson-03', title: '第3课：单股研究入门（5级深度）', file: 'phase-01-discover/lesson-03-single-analysis-basic.md', order: 3 },
      { id: 'lesson-04', title: '第4课：单股研究进阶·多情景辩论', file: 'phase-01-discover/lesson-04-single-analysis-multi-scenario.md', order: 4 },
      { id: 'lesson-05', title: '第5课：发现阶段的常见错误', file: 'phase-01-discover/lesson-05-discover-pitfalls.md', order: 5 },
    ]
  },
  {
    id: 'phase-02-track',
    name: '跟踪阶段',
    icon: '📡',
    description: '用 AI 助手持续跟进值得关注的股票',
    lessonCount: 4,
    lessons: [
      { id: 'lesson-06', title: '第6课：AI 助手功能全景', file: 'phase-02-track/lesson-06-assistant-overview.md', order: 6 },
      { id: 'lesson-07', title: '第7课：用 AI 助手做行业研究', file: 'phase-02-track/lesson-07-industry-research.md', order: 7 },
      { id: 'lesson-08', title: '第8课：AI 助手驱动日常研究闭环', file: 'phase-02-track/lesson-08-daily-routine.md', order: 8 },
      { id: 'lesson-09', title: '第9课：定时分析与跟踪节奏', file: 'phase-02-track/lesson-09-scheduled-analysis.md', order: 9 },
    ]
  },
  {
    id: 'phase-03-position',
    name: '持仓阶段',
    icon: '📊',
    description: '买了之后怎么管理仓位与风险',
    lessonCount: 6,
    lessons: [
      { id: 'lesson-10', title: '第10课：模拟交易·先练手再真金白银', file: 'phase-03-position/lesson-10-paper-trading.md', order: 10 },
      { id: 'lesson-11', title: '第11课：持仓研究·AI 帮我监控仓位', file: 'phase-03-position/lesson-11-position-research.md', order: 11 },
      { id: 'lesson-12', title: '第12课：加减仓决策', file: 'phase-03-position/lesson-12-position-sizing.md', order: 12 },
      { id: 'lesson-13', title: '第13课：风险跟踪·止盈止损规则', file: 'phase-03-position/lesson-13-risk-tracking.md', order: 13 },
      { id: 'lesson-14', title: '第14课：持仓阶段的常见错误', file: 'phase-03-position/lesson-14-position-pitfalls.md', order: 14 },
      { id: 'lesson-15', title: '第15课：AI 助手在持仓阶段的应用', file: 'phase-03-position/lesson-15-assistant-in-position.md', order: 15 },
    ]
  },
  {
    id: 'phase-04-review',
    name: '复盘阶段',
    icon: '🧐',
    description: '卖完了怎么回顾与沉淀',
    lessonCount: 4,
    lessons: [
      { id: 'lesson-16', title: '第16课：操作复盘·这笔交易做对了吗', file: 'phase-04-review/lesson-16-trade-review.md', order: 16 },
      { id: 'lesson-17', title: '第17课：案例库沉淀', file: 'phase-04-review/lesson-17-case-library.md', order: 17 },
      { id: 'lesson-18', title: '第18课：决策结算与 alpha 评分', file: 'phase-04-review/lesson-18-settlement-alpha.md', order: 18 },
      { id: 'lesson-19', title: '第19课：网格回测·用历史检验决策质量', file: 'phase-04-review/lesson-19-grid-backtest.md', order: 19 },
    ]
  },
  {
    id: 'phase-05-short-term',
    name: '短线风格专章',
    icon: '⚡',
    description: '短线用户的平台工具链与实战',
    lessonCount: 2,
    lessons: [
      { id: 'lesson-20', title: '第20课：短线研究的平台工具链', file: 'phase-05-short-term/lesson-20-short-term-toolchain.md', order: 20 },
      { id: 'lesson-21', title: '第21课：短线实战·发现到复盘一条龙', file: 'phase-05-short-term/lesson-21-short-term-playbook.md', order: 21 },
    ]
  },
  {
    id: 'phase-06-long-term',
    name: '中长线风格专章',
    icon: '🏔️',
    description: '中长线用户的平台工具链与实战',
    lessonCount: 2,
    lessons: [
      { id: 'lesson-22', title: '第22课：中长线研究的平台工具链', file: 'phase-06-long-term/lesson-22-long-term-toolchain.md', order: 22 },
      { id: 'lesson-23', title: '第23课：中长线实战·估值修复与业绩跟踪', file: 'phase-06-long-term/lesson-23-long-term-playbook.md', order: 23 },
    ]
  },
  {
    id: 'phase-07-extension',
    name: '平台扩展',
    icon: '🛠️',
    description: '功能不够用，自己造',
    lessonCount: 2,
    lessons: [
      { id: 'lesson-24', title: '第24课：AI 工作流架构师', file: 'phase-07-extension/lesson-24-workflow-architect.md', order: 24 },
      { id: 'lesson-25', title: '第25课：Skill 中心与自定义分析', file: 'phase-07-extension/lesson-25-skill-center.md', order: 25 },
    ]
  },
  {
    id: 'phase-08-personal-plan',
    name: '个人交易计划',
    icon: '📋',
    description: '把方法论变成自己的',
    lessonCount: 5,
    lessons: [
      { id: 'lesson-26', title: '第26课：信号链路与交易信号中心', file: 'phase-08-personal-plan/lesson-26-signal-center.md', order: 26 },
      { id: 'lesson-27', title: '第27课：网格回测实战方法论', file: 'phase-08-personal-plan/lesson-27-backtest-methodology.md', order: 27 },
      { id: 'lesson-28', title: '第28课：决策日志与 lesson 沉淀', file: 'phase-08-personal-plan/lesson-28-decision-log.md', order: 28 },
      { id: 'lesson-29', title: '第29课：个人交易计划模板', file: 'phase-08-personal-plan/lesson-29-personal-plan.md', order: 29 },
      { id: 'lesson-30', title: '第30课：持续优化与成长', file: 'phase-08-personal-plan/lesson-30-continuous-growth.md', order: 30 },
    ]
  },
]

/** 全量课程分类（社区版：全部免费开放） */
export const advancedCourseCategories: CourseCategory[] = allCourseCategories

/**
 * 根据分类ID和课程ID获取课程信息
 */
export function getLesson(categoryId: string, lessonId: string): Lesson | null {
  const category = advancedCourseCategories.find(cat => cat.id === categoryId)
  if (!category) return null

  return category.lessons.find(lesson => lesson.id === lessonId) || null
}

/**
 * 根据课程文件路径获取课程信息
 */
export function getLessonByFile(file: string): { category: CourseCategory, lesson: Lesson } | null {
  for (const category of advancedCourseCategories) {
    const lesson = category.lessons.find(l => l.file === file)
    if (lesson) {
      return { category, lesson }
    }
  }
  return null
}

/**
 * 获取上一课/下一课（跨分类）
 */
export function getAdjacentLessons(
  categoryId: string,
  lessonId: string
): {
  prev: { category: CourseCategory, lesson: Lesson } | null
  next: { category: CourseCategory, lesson: Lesson } | null
} {
  const category = advancedCourseCategories.find(cat => cat.id === categoryId)
  if (!category) {
    return { prev: null, next: null }
  }

  const lessonIndex = category.lessons.findIndex(l => l.id === lessonId)
  if (lessonIndex === -1) {
    return { prev: null, next: null }
  }

  // 上一课
  let prev: { category: CourseCategory, lesson: Lesson } | null = null
  if (lessonIndex > 0) {
    prev = { category, lesson: category.lessons[lessonIndex - 1] }
  } else {
    const categoryIndex = advancedCourseCategories.findIndex(cat => cat.id === categoryId)
    if (categoryIndex > 0) {
      const prevCategory = advancedCourseCategories[categoryIndex - 1]
      if (prevCategory.lessons.length > 0) {
        prev = {
          category: prevCategory,
          lesson: prevCategory.lessons[prevCategory.lessons.length - 1]
        }
      }
    }
  }

  // 下一课
  let next: { category: CourseCategory, lesson: Lesson } | null = null
  if (lessonIndex < category.lessons.length - 1) {
    next = { category, lesson: category.lessons[lessonIndex + 1] }
  } else {
    const categoryIndex = advancedCourseCategories.findIndex(cat => cat.id === categoryId)
    if (categoryIndex < advancedCourseCategories.length - 1) {
      const nextCategory = advancedCourseCategories[categoryIndex + 1]
      if (nextCategory.lessons.length > 0) {
        next = { category: nextCategory, lesson: nextCategory.lessons[0] }
      }
    }
  }

  return { prev, next }
}
