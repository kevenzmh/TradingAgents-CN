import { describe, it, expect } from 'vitest'
import { chunk, parseCsv, parseImportText } from '../importParse'

describe('chunk', () => {
  it('按大小切分，保留余数', () => {
    expect(chunk([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]])
  })
  it('空数组返回空', () => {
    expect(chunk([], 3)).toEqual([])
  })
  it('非法 size 抛错', () => {
    expect(() => chunk([1], 0)).toThrow()
  })
})

describe('parseImportText - JSON', () => {
  it('数组', () => {
    expect(parseImportText('[{"a":1},{"a":2}]')).toEqual([{ a: 1 }, { a: 2 }])
  })
  it('按 arrayKey 取数组', () => {
    expect(parseImportText('{"messages":[{"x":1}]}', 'messages')).toEqual([{ x: 1 }])
  })
  it('单个对象视为一条', () => {
    expect(parseImportText('{"a":1}')).toEqual([{ a: 1 }])
  })
  it('NDJSON 兜底', () => {
    expect(parseImportText('{"a":1}\n{"a":2}\n')).toEqual([{ a: 1 }, { a: 2 }])
  })
  it('NDJSON 某行无效时指出行号', () => {
    expect(() => parseImportText('{"a":1}\n{bad}')).toThrow(/第 2 行/)
  })
  it('无效 JSON 报错', () => {
    expect(() => parseImportText('[{"a":1},')).toThrow(/JSON 解析失败/)
  })
  it('去掉 BOM', () => {
    expect(parseImportText('﻿[{"a":1}]')).toEqual([{ a: 1 }])
  })
  it('空内容、空数组、非对象元素', () => {
    expect(() => parseImportText('   ')).toThrow(/为空/)
    expect(() => parseImportText('[]')).toThrow(/空数组/)
    expect(() => parseImportText('[1,2]')).toThrow(/第 1 条不是对象/)
  })
})

describe('parseCsv', () => {
  it('代码与日期、报告期保持字符串（前导零不丢失）', () => {
    const rows = parseCsv('symbol,date,report_period,close\n000001,20260105,20250930,10.5')
    expect(rows).toEqual([{ symbol: '000001', date: '20260105', report_period: '20250930', close: 10.5 }])
    expect(typeof rows[0].symbol).toBe('string')
  })

  it('数字、布尔转换，空单元格省略', () => {
    const rows = parseCsv('symbol,close,volume,verified,note\n600519,1688.5,,true,')
    expect(rows[0]).toEqual({ symbol: '600519', close: 1688.5, verified: true })
  })

  it('点号表头转嵌套对象', () => {
    const rows = parseCsv('platform,symbol,author.author_name,author.followers_count,engagement.likes\nxueqiu,600519,甲,52000,380')
    expect(rows[0]).toEqual({
      platform: 'xueqiu',
      symbol: '600519',
      author: { author_name: '甲', followers_count: 52000 },
      engagement: { likes: 380 }
    })
  })

  it('keywords / hashtags 拆成数组', () => {
    const rows = parseCsv('symbol,keywords,hashtags\n600519,业绩|财报,茅台;白酒')
    expect(rows[0].keywords).toEqual(['业绩', '财报'])
    expect(rows[0].hashtags).toEqual(['茅台', '白酒'])
  })

  it('引号内的逗号、转义引号、换行', () => {
    const rows = parseCsv('symbol,content\n600519,"业绩好,看涨,""强势"""\n000001,"第一行\n第二行"')
    expect(rows[0].content).toBe('业绩好,看涨,"强势"')
    expect(rows[1].content).toBe('第一行\n第二行')
  })

  it('支持 CRLF 与 TSV、自动识别分隔符', () => {
    expect(parseCsv('symbol\tclose\r\n600519\t10').map(r => r.close)).toEqual([10])
  })

  it('跳过空行', () => {
    expect(parseCsv('symbol,close\n600519,1\n,\n000001,2')).toHaveLength(2)
  })

  it('错误：表头重复/空列名、列数过多、引号未闭合、无数据行', () => {
    expect(() => parseCsv('a,a\n1,2')).toThrow(/重复/)
    expect(() => parseCsv('a,\n1,2')).toThrow(/空列名/)
    expect(() => parseCsv('a,b\n1,2,3')).toThrow(/第 2 行/)
    expect(() => parseCsv('a,b\n"1,2')).toThrow(/未闭合/)
    expect(() => parseCsv('a,b')).toThrow(/至少需要/)
  })

  it('parseImportText 对非 JSON 文本走 CSV', () => {
    expect(parseImportText('symbol,close\n000001,9.9')).toEqual([{ symbol: '000001', close: 9.9 }])
  })
})
