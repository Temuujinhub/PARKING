// Зогсоолын төлбөрийн QR харах/татах modal
import { Copy } from 'lucide-react'
import { Modal, useToast } from '../../components/ui'
import { payUrl, qrUrl, QrImage } from './shared'
import { printedQrError } from '../../siteQr'

export default function SiteQrModal({ qrSite, onClose }) {
  const toast = useToast()
  const link = qrSite ? payUrl(qrSite) : ''
  const error = printedQrError(link)
  return (
    <Modal open={!!qrSite} onClose={onClose} title={`${qrSite?.name} — Төлбөрийн QR`}>
      {qrSite && (
        <div className="text-center space-y-4">
          {error ? <p role="alert" className="text-sm text-red-400">{error} Зогсоолын «Засах» хэсгээс засна уу.</p> : <>
          <QrImage key={link} code={qrSite.site_code} link={link}
            alt={`${qrSite.name} зогсоолын төлбөрийн QR код`} />
          <a href={qrUrl(qrSite.site_code, link)} download={`${qrSite.site_code}-pay-qr.png`}
            className="btn-primary justify-center w-full">Хэвлэх PNG татах (өндөр нягтрал)</a>
          </>}
          <div className="flex items-center gap-2 bg-surface-muted rounded-lg px-3 py-2">
            <code className="text-xs flex-1 text-left break-all">{payUrl(qrSite)}</code>
            <button disabled={!!error} className="btn-secondary py-1 px-2 disabled:opacity-50 disabled:cursor-not-allowed" aria-label="Хуулах"
              onClick={() => { navigator.clipboard.writeText(link); toast('Хуулагдлаа') }}>
              <Copy size={13} />
            </button>
          </div>
          <p className="text-sm text-slate-400">
            Энэ QR кодыг хэвлэж гарах хаалтны дэргэд байрлуулна. Жолооч утасны камераар уншуулж төлбөрөө төлнө.
          </p>
        </div>
      )}
    </Modal>
  )
}
