import "./globals.css";

export const metadata = {
  title: "Control Tower",
  description: "Foundation diagnostic queue"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <main className="shell">{children}</main>
      </body>
    </html>
  );
}
