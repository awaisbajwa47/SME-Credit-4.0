import "./globals.css";
export const metadata = {
  title: "SME Credit | Financial Intelligence",
  description: "Alternative-data credit assessment for SMEs",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
