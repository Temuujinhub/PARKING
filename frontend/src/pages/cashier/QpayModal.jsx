// QPay QR modal — кассын нэхэмжлэлийн QR харуулна
import { fmt } from '../../api'
import { Modal } from '../../components/ui'

export default function QpayModal({ qpayInfo, onClose }) {
  return (
    <Modal open={!!qpayInfo} onClose={onClose} title="QPay төлбөр">
      {qpayInfo && (
        <div className="text-center space-y-3">
          <div className="text-3xl font-bold font-mono text-accent">{fmt(qpayInfo.amount)}₮</div>
          {qpayInfo.qr_image
            ? <img src={`data:image/png;base64,${qpayInfo.qr_image}`} alt="QPay QR код" className="mx-auto w-52 h-52 rounded-lg bg-white p-2" />
            : <div className="text-sm bg-surface-muted rounded-lg p-4 font-mono break-all">{qpayInfo.qr_text}</div>}
          <div className="text-sm text-slate-400">Хэрэглэгч QPay апп-аар уншуулж төлнө. Төлөгдмөгц хаалт автоматаар нээгдэнэ.</div>
          {qpayInfo.mock && <div className="text-xs text-amber-400">MOCK горим — бодит QPay холбогдоогүй</div>}
        </div>
      )}
    </Modal>
  )
}
