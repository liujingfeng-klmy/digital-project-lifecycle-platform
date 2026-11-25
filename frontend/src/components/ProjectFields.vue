<script setup>
/**
 * 申报字段表单。新建申报与「修改申报信息」共用一份，避免两处字段、校验、
 * 下拉选项各写一套 —— 之前申报单位是自由输入，就是因为只有一处写、没人管口径。
 *
 * 来源标记：字段右侧的「材料识别 / 模型概括」小标，告诉申报人这个值不是自己填的、
 * 需要核对。材料里明写的用绿色，模型概括的用橙色，两者不能混为一谈。
 */
const form = defineModel('form', { type: Object, required: true })

const props = defineProps({
  groups: { type: Array, default: () => [] },
  sources: { type: Object, default: () => ({}) },
  categories: { type: Array, default: () => [] },
  showSensitive: { type: Boolean, default: true },
})

function sourceOf(key) {
  const s = props.sources?.[key]
  if (!s) return ''
  return s
}

function isAi(key) {
  return String(props.sources?.[key] || '').includes('模型')
}
</script>

<template>
  <el-form label-position="top">
    <el-form-item required>
      <template #label>
        项目名称
        <span v-if="sourceOf('name')" class="src" :class="{ ai: isAi('name') }">
          {{ sourceOf('name') }}
        </span>
      </template>
      <el-input v-model="form.name" placeholder="与可研报告封面一致，例如：市不动产登记信息平台升级改造项目" />
    </el-form-item>

    <div class="row2">
      <el-form-item required>
        <template #label>
          申报单位
          <span v-if="sourceOf('unit')" class="src" :class="{ ai: isAi('unit') }">
            {{ sourceOf('unit') }}
          </span>
        </template>
        <el-select
          v-model="form.unit"
          filterable
          clearable
          placeholder="从市本级单位名录中选择"
          style="width: 100%"
          no-data-text="名录未加载"
        >
          <el-option-group v-for="g in groups" :key="g.key" :label="g.label">
            <el-option v-for="u in g.items" :key="u.id" :label="u.name" :value="u.name">
              <span>{{ u.name }}</span>
              <span v-if="u.subgroup" class="opt-sub">{{ u.subgroup }}</span>
            </el-option>
          </el-option-group>
        </el-select>
      </el-form-item>
      <el-form-item label="申报年度">
        <el-input-number
          v-model="form.year"
          :min="2000"
          :max="2100"
          controls-position="right"
          style="width: 100%"
        />
      </el-form-item>
    </div>

    <div class="row2">
      <el-form-item required>
        <template #label>联系人</template>
        <el-input v-model="form.contact_name" maxlength="20" placeholder="本项目经办人姓名" />
      </el-form-item>
      <el-form-item required>
        <template #label>联系人手机号</template>
        <el-input
          v-model="form.contact_phone"
          maxlength="11"
          placeholder="11 位手机号，件到了、被退回都发短信到这里"
        />
      </el-form-item>
    </div>

    <div class="row2">
      <el-form-item>
        <template #label>
          业务领域
          <span v-if="sourceOf('category')" class="src" :class="{ ai: isAi('category') }">
            {{ sourceOf('category') }}
          </span>
        </template>
        <el-select v-model="form.category" placeholder="选择或输入" filterable allow-create style="width: 100%">
          <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <template #label>
          建设周期（月）
          <span v-if="sourceOf('duration_months')" class="src" :class="{ ai: isAi('duration_months') }">
            {{ sourceOf('duration_months') }}
          </span>
        </template>
        <el-input-number
          v-model="form.duration_months"
          :min="1"
          :max="120"
          controls-position="right"
          style="width: 100%"
        />
      </el-form-item>
    </div>

    <div class="row2">
      <el-form-item required>
        <template #label>
          总投资（万元）
          <span v-if="sourceOf('investment')" class="src" :class="{ ai: isAi('investment') }">
            {{ sourceOf('investment') }}
          </span>
        </template>
        <el-input-number
          v-model="form.investment"
          :min="0"
          :precision="2"
          controls-position="right"
          style="width: 100%"
        />
      </el-form-item>
      <div class="tip-cell">
        投资额要与概算合计对得上，预审会做勾稽；差 5% 以上会被退回。
      </div>
    </div>

    <el-form-item>
      <template #label>
        建设内容（至少 30 字，供预审查重与评审对照）
        <span v-if="sourceOf('summary')" class="src" :class="{ ai: isAi('summary') }">
          {{ sourceOf('summary') }}
        </span>
      </template>
      <el-input
        v-model="form.summary"
        type="textarea"
        :rows="3"
        placeholder="建什么、覆盖哪些业务、服务多少对象"
      />
    </el-form-item>

    <el-form-item>
      <template #label>
        建设必要性
        <span v-if="sourceOf('necessity')" class="src" :class="{ ai: isAi('necessity') }">
          {{ sourceOf('necessity') }}
        </span>
      </template>
      <el-input v-model="form.necessity" type="textarea" :rows="2" placeholder="现状问题、不建会怎样" />
    </el-form-item>

    <el-form-item>
      <template #label>
        政策依据
        <span v-if="sourceOf('basis')" class="src" :class="{ ai: isAi('basis') }">
          {{ sourceOf('basis') }}
        </span>
      </template>
      <el-input v-model="form.basis" placeholder="文件名称或规划名称，多个用分号隔开" />
    </el-form-item>

    <el-form-item v-if="showSensitive" label="涉敏材料">
      <el-switch v-model="form.sensitive" active-text="标记为涉敏，材料不出网" />
      <div class="tip-cell" style="padding-top: 6px">
        勾选后只跑规则预审，AI 深度审查会被拒绝，章节文本不会发送给任何外部模型。
        另：上传材料预解析默认仅用本地规则识别，不会外发正文。
      </div>
    </el-form-item>
  </el-form>
</template>

<style scoped>
.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.tip-cell { font-size: 11.5px; color: var(--ink-4); line-height: 1.8; padding-top: 30px; }
.src {
  margin-left: 6px; padding: 0 5px; border-radius: 3px; font-size: 12px;
  font-weight: 400; line-height: 16px; vertical-align: 1px;
  background: var(--ok-bg); color: var(--ok); border: 1px solid transparent;
}
.src.ai { background: var(--warn-bg); color: var(--warn); }
.opt-sub { float: right; color: var(--ink-4); font-size: 12px; margin-left: 8px; }
</style>
