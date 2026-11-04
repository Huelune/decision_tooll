/* branch_cov.py 비교용 예시. 실제 커버리지 도구 결과와 맞춰 본다. */
int example(int a, int c, int x, int mode)
{
    int r = 0;

    if (a == 1) {
        r = 1;
    }

    if ((c == x) && (c > 10)) {
        r += 10;
    } else if (c < 10) {
        r += 20;
    } else {
        r += 30;
    }

    switch (mode) {
    case 1:
        r += 100;
        break;
    case 2:
        if (x > 3) {
            r += 200;
        }
        break;
    case 3:     /* case 4로 흘러감 */
    case 4:
        r += 400;
        break;
    default:
        break;
    }

    while (r > 100) {
        r -= 100;
    }
    return r;
}
