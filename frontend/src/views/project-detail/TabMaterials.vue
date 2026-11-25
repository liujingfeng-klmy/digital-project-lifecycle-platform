<script setup>
import { computed, ref } from 'vue'
import { fmtMoney, fmtTime } from '@/utils/projectFormat'
import { materialRowsOf, materialRowCountOf, reviewGroupsOf } from '@/utils/projectDetailModel'
import {
  downloadDoc as downloadDocApi,
  viewReviewFile as viewReviewFileApi,
  viewScan as viewScanApi,
} from '@/utils/projectFiles'

/* 项目资料：这个件从申报到结项收到的全部材料，按办理流程排。
 * 一段一段各按各的顺序摞（文件按 id、文书按生成时间倒序）会变成
 * 「单位报的一堆在前、系统文书一堆在后」，归集证明跑到批复前面，
 * 跟卷宗里的现实顺序完全对不上。 */
const props = defineProps({
  p: { type: Object, required: true },
  canUpload: Boolean,
  uploadLabel: { type: String, default: '上传申报材料' },
})
const emit = defineEmits(['open-raw', 'open-upload'])

const materialRows = computed(() => materialRowsOf(props.p))
const materialCount = computed(() => materialRowCountOf(props.p))
const reviewGroups = computed(() => reviewGroupsOf(props.p))

/* 评审会材料一份就直说文件名，多份折成一条，点开才列 —— 这一页要回答
   「有没有专家意见」，不是数表格 */
const openReviewGroup = ref('')
const toggleReviewGroup = (kind) => {
  openReviewGroup.value = openReviewGroup.value === kind ? '' : kind
}
const groupSize = (g) => (g.files.reduce((s, f) => s + Number(f.size || 0), 0) / 1048576).toFixed(2)

const askUpload = () => emit('open-upload')
const openRaw = (row) => emit('open-raw', row)
const downloadDoc = (doc) => downloadDocApi(props.p.id, doc)
const viewReviewFile = (f) => viewReviewFileApi(props.p.id, f)
const viewScan = () => viewScanApi(props.p.id, props.p.review_scan)
</script>

<template>
<div class="tab-pane">
  <div v-if="!materialCount" class="pane-empty">还没有项目资料。</div>
  <div v-else class="mat-list">
    <div v-for="row in materialRows" :key="row.key" class="mat-row">
      <span class="mat-kind" :class="{ doc: row.kind === 'doc' }">{{ row.kindLabel }}</span>
      <div class="mat-main">
        <!-- 单位申报/补正交的原件 -->
        <template v-if="row.kind === 'file'">
          <button class="mat-name" @click="openRaw(row.data)">{{ row.data.filename }}</button>
          <div class="mat-sub">
            {{ row.data.size_mb }} MB
            <span v-if="row.data.stage_label" class="mat-stage">{{ row.data.stage_label }}</span>
            <span v-if="row.data.version > 1" class="mat-ver">第 {{ row.data.version }} 版</span>
          </div>
        </template>
        <!-- 系统按模板生成的文书 -->
        <template v-else-if="row.kind === 'doc'">
          <button class="mat-name" @click="downloadDoc(row.data)">{{ row.data.name }}</button>
          <div class="mat-sub">
            系统文书
            <span v-if="row.data.stage_label" class="mat-stage">{{ row.data.stage_label }}</span>
          </div>
        </template>
        <!-- 评审会材料：一份就直说文件名，多份折成一条（五位专家不该占五行），
             点开才列出各份 —— 这一页要回答「有没有专家意见」，不是数表格 -->
        <template v-else-if="row.kind === 'review'">
          <button v-if="row.group.files.length === 1" class="mat-name"
            @click="viewReviewFile(row.group.files[0])">
            {{ row.group.files[0].name }}
          </button>
          <button v-else class="mat-name" @click="toggleReviewGroup(row.group.kind)">
            {{ row.group.label }} · {{ row.group.files.length }} 份
            <span class="mat-caret">{{ openReviewGroup === row.group.kind ? '▴' : '▾' }}</span>
          </button>
          <div class="mat-sub">
            {{ row.group.files.length > 1 ? '共 ' + groupSize(row.group) + ' MB' : groupSize(row.group) + ' MB' }}
            <span class="mat-stage">评审会</span>
          </div>
          <div v-if="row.group.files.length > 1 && openReviewGroup === row.group.kind" class="mat-children">
            <div v-for="f in row.group.files" :key="f.id" class="mat-child">
              <button class="mat-child-name" @click="viewReviewFile(f)">{{ f.name }}</button>
              <span class="mat-child-size">{{ (Number(f.size || 0) / 1048576).toFixed(2) }} MB</span>
            </div>
          </div>
        </template>
        <!-- 老项目只有单份扫描件时的兼容行 -->
        <template v-else>
          <button class="mat-name" @click="viewScan()">{{ p.review_scan.name }}</button>
          <div class="mat-sub">
            改造前上传的单份扫描件
            <span class="mat-stage">评审会</span>
          </div>
        </template>
      </div>
    </div>
  </div>
  <div class="pane-foot">
    <el-button v-if="canUpload" type="primary" @click="askUpload()">
      {{ uploadLabel }}
    </el-button>
  </div>
</div>
</template>

<style scoped>
@import 'base.css';

/* 申报材料清单 */
.mat-list {
  display: flex;
  flex-direction: column;
}

.mat-row {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 12px 0;
  border-bottom: 1px dashed var(--line-soft);
}

.mat-row:last-child {
  border-bottom: 0;
}

/* 类型列固定宽度：标签字数不一样（立项批复 4 字、费用评审任务书 7 字），
   自动宽度会让后面每份材料名各起各的头，看着像错位 —— 定宽后文件名的左边缘一条线 */
.mat-kind {
  flex: 0 0 104px;
  width: 104px;
  box-sizing: border-box;
  margin-top: 1px;
  font-size: 12px;
  line-height: 18px;
  padding: 0 8px;
  border-radius: 4px;
  background: var(--brand-bg);
  color: var(--brand-deep);
  text-align: left;
  white-space: nowrap;
}

.mat-kind.doc {
  background: var(--panel-3);
  color: var(--ink-3);
}

.mat-main {
  flex: 1;
  min-width: 0;
}

.mat-name {
  border: 0;
  background: none;
  padding: 0;
  font-family: inherit;
  font-size: 13.5px;
  line-height: 1.5;
  color: var(--ink);
  text-align: left;
  word-break: break-all;
  cursor: pointer;
}

.mat-name:hover {
  color: var(--brand);
  text-decoration: underline;
}

.mat-sub {
  font-size: 12px;
  color: var(--ink-4);
  margin-top: 3px;
}

.mat-ver {
  margin-left: 6px;
}

/* 材料是哪个环节交的：跨项目资料页下线后，这行信息并到清单里，
   免得同一份可研分不清是申报时交的还是补正时换的 */
.mat-stage {
  margin-left: 6px;
  padding: 0 5px;
  border-radius: 3px;
  background: var(--panel-3);
  color: var(--ink-3);
}

/* 归成一条的评审会材料：点开才列出各份文件名 */
.mat-caret {
  margin-left: 4px;
  font-size: 11px;
  color: var(--ink-4);
}

.mat-children {
  margin: 8px 0 2px;
  padding-left: 12px;
  border-left: 2px solid var(--line-soft);
}

.mat-child {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 3px 0;
}

.mat-child-name {
  border: 0;
  background: none;
  padding: 0;
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--ink-2);
  text-align: left;
  word-break: break-all;
  cursor: pointer;
}

.mat-child-name:hover {
  color: var(--brand);
  text-decoration: underline;
}

.mat-child-size {
  flex: 0 0 auto;
  margin-left: auto;
  font-size: 11.5px;
  color: var(--ink-4);
}

.pane-empty {
  padding: 26px 0;
  text-align: center;
  font-size: 13px;
  color: var(--ink-3);
}

.pane-foot {
  margin-top: 18px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.pane-foot .el-button + .el-button {
  margin-left: 0;
}
</style>
