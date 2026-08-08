"""TEMPORARY probe fixture generator (deleted after use)."""

from reportlab.lib import pdfencrypt
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# 31 pages -> exceeds MAX_PAGES=30
c = canvas.Canvas("/tmp/pages31.pdf", pagesize=A4)
for i in range(31):
    c.setFont("Helvetica", 10)
    for j in range(20):
        c.drawString(
            56,
            780 - j * 14,
            f"Requirements line {j} on page {i}: Kubernetes Terraform Go Python production experience at scale.",
        )
    c.showPage()
c.save()

# encrypted
enc = pdfencrypt.StandardEncryption(userPassword="userpass", ownerPassword="ownerpass", canPrint=0)
c = canvas.Canvas("/tmp/encrypted.pdf", pagesize=A4, encrypt=enc)
c.setFont("Helvetica", 10)
for j in range(30):
    c.drawString(
        56,
        780 - j * 14,
        f"Line {j}: Kubernetes Terraform production experience requirements section.",
    )
c.save()
print("ok")
