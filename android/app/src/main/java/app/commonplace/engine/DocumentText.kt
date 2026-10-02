package app.commonplace.engine

import android.graphics.pdf.PdfRenderer
import android.graphics.pdf.PdfRendererPreV
import android.os.Build
import android.os.ParcelFileDescriptor
import android.os.ext.SdkExtensions
import androidx.annotation.RequiresApi
import java.io.InputStream

/** Text of a document the user adds to "My documents", one string per page. */
object DocumentText {
    /** Larger files are refused before they are read. */
    const val MAX_BYTES = 50L * 1024 * 1024

    /** The platform PDF text API: Android 15, or Android 12 to 14 with SDK extension 13. */
    val pdfSupported: Boolean
        get() = Build.VERSION.SDK_INT >= 35 || (Build.VERSION.SDK_INT >= 31 && SdkExtensions.getExtensionVersion(Build.VERSION_CODES.S) >= 13)

    /** Pages of a PDF with a text layer. A page without text gives an empty string. */
    fun pdfPages(fd: ParcelFileDescriptor): List<String> {
        check(pdfSupported) { "PDF import needs Android 15 or later" }
        return if (Build.VERSION.SDK_INT >= 35) renderer(fd) else preVRenderer(fd)
    }

    @RequiresApi(35)
    private fun renderer(fd: ParcelFileDescriptor): List<String> = PdfRenderer(fd).use { r ->
        List(r.pageCount) { i -> r.openPage(i).use { p -> p.textContents.joinToString("\n") { it.text } } }
    }

    // The pre-V class has the same shape as PdfRenderer but no shared interface.
    @RequiresApi(31)
    private fun preVRenderer(fd: ParcelFileDescriptor): List<String> = PdfRendererPreV(fd).use { r ->
        List(r.pageCount) { i -> r.openPage(i).use { p -> p.textContents.joinToString("\n") { it.text } } }
    }

    /** UTF-8 text. A form feed starts a new page; without one the file is a single page. */
    fun textPages(input: InputStream): List<String> = input.readBytes().toString(Charsets.UTF_8).split('\u000C')
}
