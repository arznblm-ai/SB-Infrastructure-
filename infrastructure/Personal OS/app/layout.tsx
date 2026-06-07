import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Personal OS Finance",
  description: "Local-first RUB cashflow cockpit"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
