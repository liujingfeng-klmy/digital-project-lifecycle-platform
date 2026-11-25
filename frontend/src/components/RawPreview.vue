<script setup>
/** 原件页内预览。只接 blob URL，取文件的事交给 useRawFile。 */
defineProps({
  modelValue: { type: Boolean, default: false },
  url: { type: String, default: '' },
  filename: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="filename || '原件预览'"
    width="84%"
    top="4vh"
    class="raw-dialog"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="raw-wrap">
      <iframe v-if="url" :src="url" class="raw-frame" title="原件预览" />
      <div v-else class="raw-empty">原件正在加载…</div>
    </div>
    <template #footer>
      <span class="raw-tip">此处为原件本身，内容与解析结果无关；关闭后即释放临时缓存。</span>
    </template>
  </el-dialog>
</template>

<style scoped>
.raw-wrap {
  height: 74vh;
  background: var(--panel-2);
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  overflow: hidden;
}
.raw-frame {
  width: 100%;
  height: 100%;
  border: 0;
  background: #fff;
}
.raw-empty {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--ink-3);
  font-size: 13px;
}
.raw-tip {
  font-size: 11.5px;
  color: var(--ink-3);
}
</style>
