import { ChevronRight, FolderOpen, HardDrive, Languages } from 'lucide-react'
import { useI18n } from '../../i18n'
import HubDialog from './HubDialog'

type Props = {
  show: boolean
  onClose: () => void
  workspace?: string
  onChangeWorkspace?: () => void
  /** Memory-area root from GET /defaults.appdata (read-only). */
  appdata?: string
  onOpenAdvancedSettings?: () => void
}

function pathLabel(path: string): string {
  const p = path.replace(/\\/g, '/').replace(/\/+$/, '')
  const parts = p.split('/').filter(Boolean)
  return parts[parts.length - 1] || p || '未选择'
}

/** Settings dialog — workspace switch + advanced settings entry. */
export default function HubSettingsPanel({
  show,
  onClose,
  workspace,
  onChangeWorkspace,
  appdata,
  onOpenAdvancedSettings,
}: Props) {
  const { t, language, setLanguage } = useI18n()
  return (
    <HubDialog
      show={show}
      title={(
        <div className="hub-models-title">
          <span>{t('app.settings')}</span>
          <button
            type="button"
            className="hub-link"
            onClick={() => onOpenAdvancedSettings?.()}
          >
            {t('app.advancedSettings')}
          </button>
        </div>
      )}
      width={480}
      onClose={onClose}
      actions={<button type="button" className="hub-btn primary" onClick={onClose}>{t('app.close')}</button>}
    >
      <section className="hub-settings-section">
        <div className="hub-settings-row hub-settings-language">
          <span className="hub-settings-workspace-icon" aria-hidden="true">
            <Languages size={18} />
          </span>
          <span>
            <strong>{t('app.language')}</strong>
            <em>{language === 'zh-CN' ? t('app.languageZh') : language === 'zh-TW' ? t('app.languageZhTw') : t('app.languageEn')}</em>
          </span>
          <div className="hub-language-switch" role="group" aria-label={t('app.language')}>
            <button
              type="button"
              className={`hub-btn${language === 'zh-CN' ? ' primary' : ''}`}
              onClick={() => setLanguage('zh-CN')}
            >
              {t('app.languageZh')}
            </button>
            <button
              type="button"
              className={`hub-btn${language === 'zh-TW' ? ' primary' : ''}`}
              onClick={() => setLanguage('zh-TW')}
            >
              {t('app.languageZhTw')}
            </button>
            <button
              type="button"
              className={`hub-btn${language === 'en-US' ? ' primary' : ''}`}
              onClick={() => setLanguage('en-US')}
            >
              {t('app.languageEn')}
            </button>
          </div>
        </div>
        {onChangeWorkspace ? (
          <button
            type="button"
            className="hub-settings-row hub-settings-workspace"
            onClick={() => {
              onClose()
              onChangeWorkspace()
            }}
          >
            <span className="hub-settings-workspace-icon" aria-hidden="true">
              <FolderOpen size={18} />
            </span>
            <span>
              <strong>{t('app.switchWorkspace')}</strong>
              <em title={workspace || undefined}>
                {workspace ? pathLabel(workspace) : t('app.selectLocalDir')}
              </em>
            </span>
            <ChevronRight size={16} className="hub-settings-row-chevron" />
          </button>
        ) : (
          <p className="hub-settings-workspace-path">{workspace || t('app.workspacePath')}</p>
        )}
        {workspace && onChangeWorkspace ? (
          <p className="hub-settings-workspace-path" title={workspace}>{workspace}</p>
        ) : null}
        <p className="hub-settings-foot">{t('app.workspaceFoot')}</p>
        {appdata ? (
          <div className="hub-settings-row hub-settings-workspace" aria-label={t('app.appdataLabel')}>
            <span className="hub-settings-workspace-icon" aria-hidden="true">
              <HardDrive size={18} />
            </span>
            <span>
              <strong>{t('app.appdataLabel')}</strong>
              <em title={appdata}>{pathLabel(appdata)}</em>
            </span>
          </div>
        ) : null}
        {appdata ? (
          <p className="hub-settings-workspace-path" title={appdata}>{appdata}</p>
        ) : null}
      </section>
    </HubDialog>
  )
}
