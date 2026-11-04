/**
 * 数据导入：把用户粘贴 / 上传的文本解析成记录数组，并按批次切分。
 *
 * 支持三种输入：
 * - JSON：数组 `[...]`，或 `{ "<arrayKey>": [...] }`，或单个对象（按一条处理）
 * - NDJSON：每行一个 JSON 对象
 * - CSV / TSV：首行为表头；表头支持点号嵌套（`author.author_name`）
 *
 * CSV 的类型处理要点：
 * - 代码、日期、报告期、ID 等字段**强制保持字符串**，否则 `000001` 会变成 `1`、`20250930` 变成数字
 * - 其余形如数字的单元格转为 number，`true/false` 转为 boolean，空单元格省略该字段
 * - `keywords` / `hashtags` 用 `|` 或 `;` 分隔成数组
 */

/** CSV 中必须保持字符串的列 */
const STRING_KEYS = new Set([
  'symbol', 'code', 'report_period', 'date', 'trade_date', 'list_date', 'message_id', 'platform',
  'publish_time', 'url', 'title', 'content', 'name', 'area', 'industry', 'author_id', 'author.author_id',
  'author.author_name', 'source', 'market', 'language'
])
const ARRAY_KEYS = new Set(['keywords', 'hashtags'])

export function chunk<T>(items: T[], size: number): T[][] {
  if (size <= 0) throw new Error('chunk size 必须大于 0')
  const out: T[][] = []
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size))
  return out
}

/** 解析 CSV/TSV 为二维数组（处理引号、转义引号、CRLF、引号内换行） */
function splitRows(text: string, delimiter: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let cell = ''
  let inQuotes = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          cell += '"'
          i++
        } else {
          inQuotes = false
        }
      } else {
        cell += ch
      }
      continue
    }
    if (ch === '"') {
      inQuotes = true
    } else if (ch === delimiter) {
      row.push(cell)
      cell = ''
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++
      row.push(cell)
      rows.push(row)
      row = []
      cell = ''
    } else {
      cell += ch
    }
  }
  if (inQuotes) throw new Error('CSV 存在未闭合的引号')
  if (cell !== '' || row.length > 0) {
    row.push(cell)
    rows.push(row)
  }
  return rows
}

function detectDelimiter(firstLine: string): string {
  const candidates = [',', '\t', ';']
  let best = ','
  let bestCount = -1
  for (const d of candidates) {
    const n = firstLine.split(d).length - 1
    if (n > bestCount) {
      best = d
      bestCount = n
    }
  }
  return best
}

function coerce(key: string, raw: string): unknown {
  const value = raw.trim()
  if (ARRAY_KEYS.has(key)) {
    return value.split(/[|;]/).map(s => s.trim()).filter(Boolean)
  }
  if (STRING_KEYS.has(key)) return value
  if (/^-?\d+(\.\d+)?([eE][+-]?\d+)?$/.test(value)) return Number(value)
  if (value === 'true' || value === 'TRUE') return true
  if (value === 'false' || value === 'FALSE') return false
  return value
}

function setPath(target: Record<string, unknown>, path: string, value: unknown) {
  const parts = path.split('.')
  let cur = target
  for (let i = 0; i < parts.length - 1; i++) {
    const p = parts[i]
    if (typeof cur[p] !== 'object' || cur[p] === null) cur[p] = {}
    cur = cur[p] as Record<string, unknown>
  }
  cur[parts[parts.length - 1]] = value
}

export function parseCsv(text: string): Record<string, unknown>[] {
  const firstLine = text.split(/\r?\n/, 1)[0]
  const rows = splitRows(text, detectDelimiter(firstLine))
  if (rows.length < 2) throw new Error('CSV 至少需要表头行和一行数据')
  const headers = rows[0].map(h => h.trim())
  if (headers.some(h => !h)) throw new Error('CSV 表头存在空列名')
  if (new Set(headers).size !== headers.length) throw new Error('CSV 表头存在重复列名')

  const records: Record<string, unknown>[] = []
  for (let r = 1; r < rows.length; r++) {
    const cells = rows[r]
    if (cells.every(c => c.trim() === '')) continue
    if (cells.length > headers.length) {
      throw new Error(`CSV 第 ${r + 1} 行有 ${cells.length} 列，多于表头的 ${headers.length} 列（字段内含逗号请加引号）`)
    }
    const record: Record<string, unknown> = {}
    headers.forEach((h, idx) => {
      const raw = cells[idx]
      if (raw === undefined || raw.trim() === '') return
      setPath(record, h, coerce(h, raw))
    })
    records.push(record)
  }
  return records
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/**
 * 把文本解析为记录数组。无法解析时抛出带位置信息的 Error。
 * @param arrayKey 当输入是 `{ "<arrayKey>": [...] }` 时取该键
 */
export function parseImportText(text: string, arrayKey?: string): Record<string, unknown>[] {
  const content = text.replace(/^﻿/, '').trim()
  if (!content) throw new Error('内容为空')

  let result: unknown
  if (content.startsWith('[') || content.startsWith('{')) {
    try {
      result = JSON.parse(content)
    } catch (jsonErr: any) {
      // NDJSON 兜底：每行一个对象
      const lines = content.split(/\r?\n/).map(l => l.trim()).filter(Boolean)
      if (lines.length > 1 && lines.every(l => l.startsWith('{'))) {
        result = lines.map((line, i) => {
          try {
            return JSON.parse(line)
          } catch (e: any) {
            throw new Error(`第 ${i + 1} 行 JSON 无效: ${e.message}`)
          }
        })
      } else {
        throw new Error(`JSON 解析失败: ${jsonErr.message}`)
      }
    }
  } else {
    result = parseCsv(content)
  }

  if (isPlainObject(result)) {
    if (arrayKey && Array.isArray(result[arrayKey])) result = result[arrayKey]
    else result = [result]
  }
  if (!Array.isArray(result)) throw new Error('数据必须是数组')
  if (result.length === 0) throw new Error('数据为空数组')
  const badIndex = result.findIndex(item => !isPlainObject(item))
  if (badIndex >= 0) throw new Error(`第 ${badIndex + 1} 条不是对象`)
  return result as Record<string, unknown>[]
}
