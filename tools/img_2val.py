import cv2

# 读取图片
img = cv2.imread("1.png")

# 转为灰度图
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# 保存灰度图
cv2.imwrite("2.png", gray)

# 显示
cv2.imshow("Gray Image", gray)
cv2.waitKey(0)
cv2.destroyAllWindows()