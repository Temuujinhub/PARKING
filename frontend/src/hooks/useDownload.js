// Excel/файл татах дундын hook — blob авч татаж, алдааг toast-оор харуулна
import { api } from '../api'
import { useToast } from '../components/ui'

export function useDownload() {
  const toast = useToast()
  return async (path, filename) => {
    try {
      const blob = await api(path, { blob: true })
      const url = URL.createObjectURL(blob)
      Object.assign(document.createElement('a'), { href: url, download: filename }).click()
      URL.revokeObjectURL(url)
    } catch (e) { toast(e.message, 'error') }
  }
}
