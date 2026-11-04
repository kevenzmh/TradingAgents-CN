<template>
  <div class="rule-list">
    <div class="head">
      <strong>{{ title }}</strong>
      <el-button size="small" :icon="Plus" @click="add">添加</el-button>
    </div>
    <div v-if="!model.length" class="empty">暂无，点击“添加”</div>
    <div v-for="(item, i) in model" :key="i" class="row">
      <el-input v-model="item[field]" :placeholder="placeholder" class="main" />
      <el-input v-model="item.description" placeholder="说明（可选）" class="desc" />
      <el-button link type="danger" :icon="Delete" @click="remove(i)" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Delete, Plus } from '@element-plus/icons-vue'

const props = defineProps<{ modelValue: any[]; title: string; field: string; placeholder?: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: any[]): void }>()

// 条目为对象（父组件在载入时已把旧数据里的纯字符串条目规整成对象），v-model 直接改对象字段
const model = computed<any[]>(() => props.modelValue)

function add() {
  emit('update:modelValue', [...model.value, { [props.field]: '', description: '' }])
}

function remove(i: number) {
  emit('update:modelValue', model.value.filter((_, idx) => idx !== i))
}
</script>

<style scoped lang="scss">
.rule-list {
  margin-bottom: 18px;

  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }

  .empty {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  .row {
    display: flex;
    gap: 8px;
    margin-bottom: 8px;

    .main {
      flex: 3;
    }

    .desc {
      flex: 2;
    }
  }
}
</style>
