// A collected payment, remaining parking balance and gate ACK are distinct.
export function paymentOutcome(result = {}) {
  const due = Number(result.amount_due)
  if (result.needs_additional_payment || (Number.isFinite(due) && due > 0)) {
    return { kind: 'partial', title: 'Төлбөр бүртгэгдлээ — үлдэгдэл байна',
      message: `Үлдэгдэл ${Number.isFinite(due) ? due.toLocaleString('mn-MN') + '₮' : 'дүнг шалгана уу'}. Өмнө төлсөн дүн хасагдсан.`,
      needsBalanceRefresh: true }
  }
  if (result.barrier_command_status === 'SUCCESS') {
    return { kind: 'acknowledged', title: 'Төлбөр бүрэн төлөгдлөө',
      message: 'Хаалт нээх хүсэлт амжилттай. Хаалт нээгдсэний дараа гарна уу.' }
  }
  if (['PENDING', 'UNKNOWN', 'FAILED'].includes(result.barrier_command_status)) {
    return { kind: 'gate_attention', title: 'Төлбөр бүртгэгдлээ',
      message: 'Хаалт нээгдсэн нь баталгаажаагүй байна. Дахин төлөхгүйгээр төлөвийг шалгах эсвэл операторт хандана уу.' }
  }
  if (['CLOSED', 'MANUAL_CLOSED', 'CANCELLED'].includes(result.session_status)) {
    return { kind: 'closed', title: 'Төлбөр бүртгэгдлээ',
      message: 'Зогсолт хаагдсан байна. Энэ төлөлтөөр хаалт шинээр нээхгүй; шаардлагатай бол операторт хандана уу.' }
  }
  if (result.session_status === 'PAID') {
    const deadline = result.exit_deadline ? new Date(/Z$|[+-]\d\d:\d\d$/.test(result.exit_deadline) ? result.exit_deadline : result.exit_deadline + 'Z') : null
    const until = deadline && !Number.isNaN(deadline.getTime()) ? ` ${deadline.toLocaleTimeString('mn-MN', {hour:'2-digit',minute:'2-digit'})} хүртэл гарах камерт очно уу.` : ''
    return { kind: 'ready', title: 'Төлбөр бүрэн төлөгдлөө',
      message: `Төлсөн хүлээлтэд бүртгэлтэй.${until} Гарах камерт дугаараа уншуулна уу. Хаалт нээгдэхгүй бол дахин төлөхгүйгээр операторт хандана уу.` }
  }
  return { kind: 'unknown', title: 'Төлбөр бүртгэгдлээ',
    message: 'Гарцын төлөвийг шалгана уу. Хаалт нээгдэхгүй бол дахин төлөхгүйгээр операторт хандана уу.' }
}
