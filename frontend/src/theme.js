// Өдөр/шөнийн горимын удирдлага
const KEY = 'parking_theme'

export const isDark = () => document.documentElement.classList.contains('dark')

export function toggleTheme() {
  const dark = !isDark()
  document.documentElement.classList.toggle('dark', dark)
  localStorage.setItem(KEY, dark ? 'dark' : 'light')
  return dark
}
