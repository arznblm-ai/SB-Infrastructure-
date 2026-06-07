export function money(amount: number) {
  return new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: 0
  }).format(Math.round(amount)) + " ₽";
}
