import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DriverQ",
  description: "Scene explorer and scenario query tool for NuScenes",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
