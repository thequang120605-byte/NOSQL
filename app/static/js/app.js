function escapeHtml(value) {
    const span = document.createElement('span');
    span.textContent = String(value);
    return span.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Common JavaScript Helpers for FoundationDB E-Commerce Demo

function formatCurrencyVND(amount) {
    return new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND' }).format(amount);
}

function showSuccessAlert(message, callback) {
    Swal.fire({
        icon: 'success',
        title: 'Thành công',
        text: message,
        timer: 1800,
        showConfirmButton: false
    }).then(() => {
        if (callback) callback();
    });
}

function showErrorAlert(message) {
    Swal.fire({
        icon: 'error',
        title: 'Có lỗi xảy ra',
        text: message,
        confirmButtonColor: '#0d6efd'
    });
}

// Function to trigger Seed Sample Data
async function seedSampleData() {
    const confirm = await Swal.fire({
        title: 'Nạp lại dữ liệu mẫu?',
        text: 'Thao tác này sẽ xóa các tiền tố (prefixes) ứng dụng và nạp lại bộ dữ liệu mẫu theo Tuple Layer FoundationDB!',
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#1d4ed8',
        cancelButtonColor: '#64748b',
        confirmButtonText: 'Đồng ý nạp lại',
        cancelButtonText: 'Hủy'
    });

    if (confirm.isConfirmed) {
        Swal.fire({
            title: 'Đang xử lý...',
            text: 'Đang ghi các bản ghi mẫu và cập nhật chỉ mục vào FoundationDB...',
            allowOutsideClick: false,
            didOpen: () => Swal.showLoading()
        });

        try {
            const resp = await fetch('/api/seed', { method: 'POST' });
            const data = await resp.json();
            if (data.status === 'success') {
                showSuccessAlert('Đã nạp thành công bộ dữ liệu mẫu!', () => {
                    window.location.reload();
                });
            } else {
                showErrorAlert(data.message || 'Lỗi nạp dữ liệu');
            }
        } catch (e) {
            showErrorAlert('Lỗi mạng hoặc server không phản hồi: ' + e.message);
        }
    }
}
