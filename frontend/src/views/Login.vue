<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const form = ref({ username: '', password: '' })
const loading = ref(false)

async function submit() {
  if (!form.value.username || !form.value.password) {
    ElMessage.warning('请填写用户名和密码')
    return
  }
  loading.value = true
  try {
    await userStore.login(form.value.username.trim(), form.value.password)
    ElMessage.success('登录成功')
    router.push(route.query.redirect || '/')
  } catch {
    /* 错误提示由拦截器统一处理 */
  } finally {
    loading.value = false
  }
}

// 演示账号只填用户名，不代填密码：口令仍由部署文档给，避免把口令写进前端
function fillUser(name) {
  form.value.username = name
}
const demoAccounts = [
  { user: 'admin', label: '管理员' },
  { user: 'shenbao', label: '申报单位' },
  { user: 'chushen', label: '初审岗' },
  { user: 'shenhe', label: '审核岗' },
]
</script>

<template>
  <div class="login-page">
    <!-- 左：品牌区。深蓝底把平台调性立住，网格纹理只在右上角微微透出 -->
    <section class="login-brand">
      <div class="login-brand-inner">
        <div class="login-logo">
          <div class="brand-mark">
            <svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true">
              <path fill="currentColor" d="M4 4h7v7H4V4zm9 0h7v7h-7V4zM4 13h7v7H4v-7zm9 0h7v7h-7v-7z" />
            </svg>
          </div>
          <div>
            <div class="login-logo-title">信息化项目管理平台</div>
            <div class="login-logo-sub">全生命周期 · 内部服务器版</div>
          </div>
        </div>

        <h1 class="login-headline">让每一个信息化项目<br /><span>全程可查、全程可溯</span></h1>
        <p class="login-desc">
          从申报、预审、评审、补正，到费用评审、财政审核、立项与验收，
          每一件都在线留痕。谁在办、停了多少天、卡在哪一环，一眼看得到。
        </p>

        <div class="login-points">
          <div class="login-point">
            <span class="login-point-icon">
              <svg class="icon" viewBox="0 0 24 24">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />
                <path d="m9 12 2 2 4-4" />
              </svg>
            </span>
            两级预审：规则勾稽 + AI 语义审查，问题逐条落到材料原文
          </div>
          <div class="login-point">
            <span class="login-point-icon">
              <svg class="icon" viewBox="0 0 24 24">
                <path d="M8 3v18" /><path d="M16 3v18" />
                <path d="m3 8 3-3 3 3" /><path d="m13 16 3 3 3-3" />
              </svg>
            </span>
            重复建设逐项对照，不用把两份可研从头翻到尾
          </div>
          <div class="login-point">
            <span class="login-point-icon">
              <svg class="icon" viewBox="0 0 24 24">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />
              </svg>
            </span>
            原件留在本机服务器；仅 AI 预审时章节文本会发往所配置的模型服务
          </div>
        </div>

        <div class="login-stats">
          <div>
            <div class="login-stat-num">14</div>
            <div class="login-stat-label">办理环节</div>
          </div>
          <div>
            <div class="login-stat-num">2 级</div>
            <div class="login-stat-label">预审深度</div>
          </div>
          <div>
            <div class="login-stat-num">全量</div>
            <div class="login-stat-label">流转留痕</div>
          </div>
        </div>

        <div class="login-foot">
          数据存放于本机服务器 · 不在浏览器留存业务数据
        </div>
      </div>
    </section>

    <!-- 右：登录表单。窄卡片居中，避免大屏上拉得过宽 -->
    <section class="login-main">
      <div class="login-mobile-brand">
        <div class="brand-mark">
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            <path fill="currentColor" d="M4 4h7v7H4V4zm9 0h7v7h-7V4zM4 13h7v7H4v-7zm9 0h7v7h-7v-7z" />
          </svg>
        </div>
        <div>
          <div class="login-logo-title">信息化项目管理平台</div>
          <div class="login-logo-sub">全生命周期 · 内部服务器版</div>
        </div>
      </div>

      <div class="login-card">
        <div class="login-card-head">
          <div class="login-card-title">登录</div>
          <div class="login-card-sub">账号由管理员统一创建，不开放自助注册</div>
        </div>

        <el-form class="login-form" label-position="top" @submit.prevent="submit">
          <el-form-item label="用户名">
            <el-input
              v-model="form.username"
              size="large"
              placeholder="请输入用户名"
              autocomplete="username"
              @keyup.enter="submit"
            />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              type="password"
              size="large"
              placeholder="请输入密码"
              show-password
              autocomplete="current-password"
              @keyup.enter="submit"
            />
          </el-form-item>
          <el-button
            type="primary"
            size="large"
            class="submit-btn"
            :loading="loading"
            @click="submit"
          >
            登 录
          </el-button>
        </el-form>

        <div class="login-divider">演示账号</div>
        <div class="demo-accounts">
          <button
            v-for="a in demoAccounts"
            :key="a.user"
            type="button"
            class="demo-account"
            @click="fillUser(a.user)"
          >
            <b>{{ a.user }}</b> · {{ a.label }}
          </button>
        </div>

        <div class="login-tip">
          点击账号可填入用户名，初始口令见部署文档。首次登录须修改初始密码。
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.login-page {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  background: var(--bg);
  overflow-y: auto;
}

/* ---------------------------------------------------------------- 品牌区 */
.login-brand {
  position: relative;
  width: 54%;
  min-height: 100vh;
  padding: 46px 54px 38px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  color: #fff;
  background: linear-gradient(150deg, #0A1F38 0%, #0E2F55 48%, #0A2442 100%);
}
.login-brand::before {
  content: "";
  position: absolute;
  width: 640px;
  height: 640px;
  top: -260px;
  right: -240px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(47, 128, 237, 0.30), transparent 66%);
}
.login-brand::after {
  content: "";
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.045) 1px, transparent 1px);
  background-size: 40px 40px;
  -webkit-mask-image: radial-gradient(90% 70% at 70% 20%, #000, transparent 80%);
  mask-image: radial-gradient(90% 70% at 70% 20%, #000, transparent 80%);
}
.login-brand-inner {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  flex: 1;
}

.login-logo { display: flex; align-items: center; gap: 12px; }
.brand-mark {
  width: 42px;
  height: 42px;
  flex: 0 0 42px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  background: linear-gradient(145deg, #3B8BF2, #1D5FAE 55%, #154B8A);
  border: 1px solid rgba(255, 255, 255, 0.18);
  box-shadow: 0 10px 24px -10px rgba(47, 128, 237, 0.75);
  color: #fff;
}
.login-logo-title { font-size: 15px; font-weight: 600; letter-spacing: 0.4px; }
.login-logo-sub {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.46);
  letter-spacing: 0.1em;
  margin-top: 2px;
}

.login-headline {
  font-size: 36px;
  font-weight: 600;
  line-height: 1.35;
  letter-spacing: -0.4px;
  margin: 74px 0 16px;
  max-width: 520px;
}
.login-headline span { color: #7FB8F5; }
.login-desc {
  font-size: 13px;
  line-height: 1.95;
  color: rgba(255, 255, 255, 0.62);
  margin: 0;
  max-width: 470px;
}

.login-points {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 34px;
  max-width: 470px;
}
.login-point {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.72);
  line-height: 1.7;
}
.login-point-icon {
  width: 24px;
  height: 24px;
  flex: 0 0 24px;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.12);
  display: grid;
  place-items: center;
  color: #8CC2FF;
}
.login-point-icon .icon { width: 13px; height: 13px; }

.login-stats {
  display: flex;
  gap: 38px;
  margin-top: auto;
  padding-top: 42px;
}
.login-stat-num { font-size: 24px; font-weight: 600; letter-spacing: -0.5px; }
.login-stat-label {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.44);
  margin-top: 2px;
  letter-spacing: 0.04em;
}

.login-foot {
  position: relative;
  z-index: 1;
  margin-top: 26px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.32);
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

/* ---------------------------------------------------------------- 表单区 */
.login-main {
  flex: 1;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 28px;
  background:
    radial-gradient(900px 500px at 100% 0%, #EAF2FC 0%, transparent 60%),
    var(--bg);
}
.login-mobile-brand {
  display: none;
  align-items: center;
  gap: 11px;
  width: min(392px, 100%);
  margin-bottom: 26px;
}
.login-mobile-brand .brand-mark { width: 38px; height: 38px; flex: 0 0 38px; border-radius: 11px; }
.login-mobile-brand .login-logo-title { color: var(--ink); }
.login-mobile-brand .login-logo-sub { color: var(--ink-3); }

.login-card { width: min(392px, 100%); }
.login-card-head { margin-bottom: 26px; }
.login-card-title { font-size: 24px; font-weight: 600; letter-spacing: -0.3px; }
.login-card-sub { font-size: 12px; color: var(--ink-3); margin-top: 6px; }

.login-form { display: flex; flex-direction: column; gap: 15px; }
.login-form :deep(.el-form-item) { margin-bottom: 0; }
.login-form :deep(.el-form-item__label) {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--ink-2);
  padding-bottom: 6px;
  line-height: 1.4;
}
.login-form :deep(.el-input__wrapper) { height: 42px; padding: 0 12px; border-radius: 9px; }

.submit-btn {
  width: 100%;
  height: 42px;
  margin-top: 4px;
  border-radius: 9px;
  font-size: 14px;
  font-weight: 500;
  letter-spacing: 0.06em;
}

.login-divider {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: var(--ink-4);
  margin: 20px 0 12px;
}
.login-divider::before,
.login-divider::after { content: ""; flex: 1; height: 1px; background: var(--line); }

.demo-accounts { display: flex; gap: 6px; flex-wrap: wrap; }
.demo-account {
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #fff;
  font-family: inherit;
  font-size: 12px;
  color: var(--ink-2);
  cursor: pointer;
  transition: 0.14s;
}
.demo-account:hover {
  border-color: var(--primary-line);
  background: var(--primary-soft);
  color: var(--primary-deep);
}
.demo-account b { font-weight: 600; }

.login-tip {
  margin-top: 18px;
  font-size: 12px;
  color: var(--ink-4);
  line-height: 1.8;
}

@media (max-width: 900px) {
  .login-brand { display: none; }
  .login-main { justify-content: flex-start; padding: 34px 22px 28px; }
  .login-mobile-brand { display: flex; }
}
</style>
