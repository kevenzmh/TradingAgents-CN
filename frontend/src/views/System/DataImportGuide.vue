<template>
  <div class="guide-page">
    <div class="page-header">
      <el-button text :icon="ArrowLeft" @click="router.push('/settings/data-import')">返回数据导入管理</el-button>
      <div class="header-actions">
        <el-radio-group v-model="currentKind" size="small" @change="switchKind">
          <el-radio-button value="stock-data">股票数据</el-radio-button>
          <el-radio-button value="social-media">社媒消息</el-radio-button>
        </el-radio-group>
        <el-button size="small" type="primary" :icon="Download" :loading="downloading" @click="handleDownload">
          下载测试程序
        </el-button>
      </div>
    </div>

    <el-card shadow="never" v-loading="loading">
      <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />
      <el-empty v-else-if="!loading && !html" description="暂无指南内容" />
      <div v-else class="markdown-body" v-html="html"></div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Download } from '@element-plus/icons-vue'

import { downloadTestProgram, getGuideMarkdown, type GuideKind } from '@/api/dataImport'
import { renderMarkdown } from '@/utils/markdown'

const props = defineProps<{ kind: GuideKind }>()
const router = useRouter()

const currentKind = ref<GuideKind>(props.kind)
const html = ref('')
const loading = ref(false)
const error = ref('')
const downloading = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  html.value = ''
  try {
    const md = await getGuideMarkdown(currentKind.value)
    html.value = renderMarkdown(md)
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '加载指南失败'
  } finally {
    loading.value = false
  }
}

function switchKind(kind: string | number | boolean | undefined) {
  const next = kind as GuideKind
  router.replace(next === 'stock-data' ? '/settings/stock-data/api-guide' : '/settings/social-media/api-guide')
}

async function handleDownload() {
  downloading.value = true
  try {
    await downloadTestProgram()
    ElMessage.success('已下载 test_batch_import_apis.py')
  } catch (e: any) {
    ElMessage.error(e?.message || '下载失败')
  } finally {
    downloading.value = false
  }
}

watch(
  () => props.kind,
  (k) => {
    currentKind.value = k
    load()
  }
)
onMounted(load)
</script>

<style scoped lang="scss">
.guide-page {
  padding: 20px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.markdown-body {
  line-height: 1.75;
  color: var(--el-text-color-primary);
  word-break: break-word;

  :deep(h1) {
    font-size: 24px;
    margin: 0 0 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--el-border-color-lighter);
  }

  :deep(h2) {
    font-size: 19px;
    margin: 28px 0 12px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--el-border-color-lighter);
  }

  :deep(h3) {
    font-size: 16px;
    margin: 20px 0 10px;
  }

  :deep(table) {
    border-collapse: collapse;
    margin: 12px 0;
    width: 100%;
    font-size: 13px;
    display: block;
    overflow-x: auto;
  }

  :deep(th),
  :deep(td) {
    border: 1px solid var(--el-border-color);
    padding: 6px 12px;
    text-align: left;
    vertical-align: top;
  }

  :deep(th) {
    background: var(--el-fill-color-light);
    font-weight: 600;
    white-space: nowrap;
  }

  :deep(code) {
    background: var(--el-fill-color);
    border-radius: 4px;
    padding: 1px 6px;
    font-size: 12.5px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }

  :deep(pre) {
    background: var(--el-fill-color-light);
    border: 1px solid var(--el-border-color-lighter);
    border-radius: 6px;
    padding: 12px 14px;
    overflow-x: auto;

    code {
      background: transparent;
      padding: 0;
    }
  }

  :deep(blockquote) {
    margin: 12px 0;
    padding: 4px 14px;
    color: var(--el-text-color-secondary);
    border-left: 4px solid var(--el-color-primary-light-5);
    background: var(--el-fill-color-lighter);
  }

  :deep(ul),
  :deep(ol) {
    padding-left: 22px;
  }
}
</style>
