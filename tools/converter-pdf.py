import opendataloader_pdf
# Batch all files in one call — each convert() spawns a JVM process, so repeated calls are slow
opendataloader_pdf.convert(
    input_path=[r"C:\Users\Michal\Desktop\easy-OTP\docs\papers\Kaczorowski-Michał_konspekt_doktoratu.pdf"],
    output_dir=r"C:\Users\Michal\Desktop\easy-OTP\docs\papers\output",
    format="markdown",
)