import os
import numpy as np
import cv2
import skimage
import argparse
from skimage.feature import local_binary_pattern, hog, haar_like_feature
from skimage.transform import integral_image
from pdt import build_tree, prediction
from sklearn.linear_model import PassiveAggressiveClassifier as clfPA
from sklearn.linear_model import LogisticRegression as clfLR
from sklearn.linear_model import SGDClassifier as clfSGD
from sklearn.linear_model import Perceptron as clfP
from sklearn.svm import SVC
from tqdm import tqdm
import pickle

EPS = 1e-12
stride = 4
classifier1 = clfPA(max_iter=1000, random_state=10, loss='squared_hinge', tol=1e-4)
classifier2 = clfLR(solver='sag', tol=1e-1)
classifier3 = clfP(tol=1e-3)
classifier4 = clfSGD(average=True, max_iter=100)
classifier5 = SVC(gamma='auto')

parser = argparse.ArgumentParser()
parser.add_argument('-f', "--frames_dir", default='new_frames/bag')
# parser.add_argument('-g', "--ground_truth_dir", default='/home/gaurav/Desktop/test/gt/bag')
parser.add_argument('-o', "--output_dir", default='test/output')
args = parser.parse_args()

frames_dir = args.frames_dir
# ground_truth_dir = args.ground_truth_dir
output_dir = args.output_dir

forest_update = 10
classifier_update = 5

# Given the location of the object in the previous frame, find all patches 
# in the s-neighborhood of it and compute their feature vectors
# img -> current frame (frame at time t)
# loc_object -> location of object in frame (t-1)
# s -> neighborhood radius
# it is assumed that location is specified as location of top left corner
def get_patches(img,loc_object, patch_size, s=35):
	# The set X_s stores the locations of the desired patches
	X_s = list()
	# patch_features stores the feature vectors corr to each patch
	patches = list()

	# coordinates of the object patch center
	x_obj, y_obj = loc_object

	r, c = img.shape
	def out_of_bounds(y,x):
		return (x<0 or y<0 or x>c or y>r)

	d = int(patch_size/2)
	# extract patches and compute their features using sliding window approach
	for y in range(y_obj-s,y_obj+s,stride):
		for x in range(x_obj-s,x_obj+s,stride):
			if(out_of_bounds(y-d,x-d) or out_of_bounds(y-d,x+d) or out_of_bounds(y+d,x-d) or out_of_bounds(y+d,x+d)):
				continue
			patch = img[y-d:y+d, x-d:x+d]
			test_point = np.resize(patch, (patch.size))
			test_point = test_point/np.linalg.norm(test_point)
			# print(test_point)
			X_s.append((x,y))
			patches.append(test_point)

	X_s = np.array(X_s)
	patches = np.array(patches)

	return X_s, patches

# generate a random sample having equal number of positive and negative samples
# total F samples are generated
# by convention last column of the data matrix consists of the labels
def random_sample(train, F=100):
	train_data = np.copy(train)
	num_pos = np.sum(train_data[:,-1]==1)
	num_neg = np.sum(train_data[:,-1]==0)

	assert num_neg>=num_pos

	num_pos = min(int(F/2), num_pos)
	num_neg = num_pos

	samples = list()

	# number of pos(neg) samples left to be picked
	# num_pos = int(F/2)
	# num_neg = int(F/2)

	np.random.shuffle(train_data)

	for sample in train_data:
		label = sample[-1]

		if((label==1 and num_pos==0) or (label==0 and num_neg==0)):
			continue

		if(label==1):
			num_pos -= 1
		else:
			num_neg -= 1

		samples.append(sample)

		if(num_pos==0 and num_neg==0):
			break

	samples = np.asarray(samples, dtype=np.float64)
	return samples


# construct a random forest having M trees
def construct_perceptron_forest(train, M=10):
	forest = list()
	# F = int(train.shape[0]/10)
	F = 1000
	for i in range(M):
		print("Tree # ",i+1)
		subset = random_sample(train,F)
		tree = build_tree(subset)
		forest.append(tree)

	return forest

# the hash code function for a test sample u
def hash_code(forest, u):
	# sum of positive posterior probabilties
	p_sum = 0.0
	# sum of negative posterior probabilties
	n_sum = 0.0
	for tree in forest:
		label, num_pos, num_samps = prediction(u, tree)
		# print (label, num_pos, num_samps)
		if(label==1):
			p_sum += (num_pos*1.0)/num_samps
		else:
			n_sum += ((num_samps-num_pos)*1.0)/num_samps


	# return 1 if p_sum-n_sum>=0.0 else -1
	return p_sum-n_sum


# generate the l-dimensional binary code vector for each test sample
def binary_codes_test(forests, test):
	def normalize_codes(vec):
		mini = np.min(vec)
		vec = vec + np.abs(mini)
		vec = vec/np.max(vec)
		return vec

	test_codes = list() # num_test x l
	# print(test.shape)
	for u in tqdm(test):
		binary_code = list() # 1 x l
		for forest in forests:
			binary_code.append(hash_code(forest, u))

		# binary_code = normalize_codes(binary_code)
		test_codes.append(binary_code)

	test_codes = np.array(test_codes)
	# print(test_codes)
	return test_codes

# train/update the perceptron forest to generate forests
def binary_codes_train(train, l=100, M=10):
	# construct M forests
	M_forests = list()
	for j in range(l):
		print("Forest # ",j+1)
		forest = construct_perceptron_forest(train)
		M_forests.append(forest)

	return M_forests

def get_pos_neg_patches(img, loc, alpha, beta, patch_size):
	# euclidean distance between two points
	e_dist = lambda p1, p2 : np.sqrt(((p2[0]-p1[0])**2 + (p2[1]-p1[1])**2))

	# coordinates all points of the frame within a given distance range from a point
	def find_points(r,c,y,x,dist1, dist2):
		# return [(i,j) for j in range(c) for i in range(r) if (e_dist((i,j),(x,y)) <= dist2 and (e_dist((i,j),(x,y)) >= dist1))]
		dist_list = list()
		for i in range(0,r,stride):
			for j in range(0,c,stride):
				dist = e_dist((i,j),(y,x))
				if(dist >= dist1 and dist < dist2):
					dist_list.append((i,j))
		return dist_list

	x_obj, y_obj = loc
	r, c = img.shape
	d = int(patch_size/2)

	def out_of_bounds(y,x):
		# print("In here:",r,c)
		return (x<0 or y<0 or x>c or y>r)

	# pos patches
	# patch_features stores the feature vectors corr to each patch
	patches_pos = list()
	alpha_dist = find_points(r,c,y_obj,x_obj,0,alpha)

	# print(loc)
	alpha_points = len(alpha_dist)
	print("Alpha points:", alpha_points)

	print("Extracting pos patches")
	# print(img.shape)
	# for point in tqdm(alpha_dist):
	for point in tqdm(alpha_dist):
		y, x = point
		# print(y,x)
		# take a patch centered at this point
		# if any part of the patch falls out of the image, ignore the patch
		if(out_of_bounds(y-d,x-d) or out_of_bounds(y-d,x+d) or out_of_bounds(y+d,x-d) or out_of_bounds(y+d,x+d)):
			# print("Really", (y,x))
			continue

		patch = img[y-d:y+d,x-d:x+d]
		train_point = np.resize(patch,(patch.size,))
		# print(patch.shape)
		train_point = train_point/np.linalg.norm(train_point)
		# train_point = compute_features(patch)
		# if(train_point.size < 800):
			# print("Culprits:",(x,y))
		# print(patch.shape)
		# print("train_point", train_point.shape)
		patches_pos.append(train_point)

	patches_pos = np.asarray(patches_pos)
	print("patches_pos", patches_pos.shape)

	# neg patches
	# patch_features stores the feature vectors corr to each patch
	patches_neg = list()
	beta_dist = find_points(r,c,y_obj,x_obj,alpha,beta)
	# beta_points = len(beta_dist)
	# beta_inds = np.random.choice(np.arange(start=0, stop=beta_points), size=alpha_points, replace=False)
	# beta_dist = list(beta_dist[beta_inds])

	print("Extracting neg patches")
	for point in tqdm(beta_dist):
		y, x = point
		# take a patch centered at this point
		# if any part of the patch falls out of the image, ignore the patch
		if(out_of_bounds(y-d,x-d) or out_of_bounds(y-d,x+d) or out_of_bounds(y+d,x-d) or out_of_bounds(y+d,x+d)):
			continue

		patch = img[y-d:y+d,x-d:x+d]
		train_point = np.resize(patch,(patch.size,))
		# print(patch.shape)
		train_point = train_point/np.linalg.norm(train_point)
		patches_neg.append(train_point)

	patches_neg = np.asarray(patches_neg)

	print("patches_neg", patches_neg.shape)
	return patches_pos, patches_neg



# construct training data for training the perceptron forest
# frame is the current frame, loc is the location of the object
# in the current frame as predicted by the classifier
def construct_training_data_forest(frame, loc, alpha, beta, patch_size):
	patches_pos, patches_neg = get_pos_neg_patches(frame, loc, alpha, beta, patch_size)
	r,c  = patches_pos.shape
	temp_pos = np.ones((r,c+1))
	temp_pos[:,:-1] = patches_pos
	train_pos = temp_pos

	print("r,c=",r,c)

	r,c  = patches_neg.shape
	temp_neg = np.zeros((r,c+1))
	temp_neg[:,:-1] = patches_neg
	train_neg = temp_neg

	train = np.concatenate((train_pos, train_neg))
	np.random.shuffle(train)

	return train

# given the current frame image and the predicted image location loc,
# extract the patches around this location, compute their codes using 
# trained the perceptron forests and form the training data for the classifier
# Training data of the classifier: all patches at distance < alpha are pos
# all patches at alpha < distance < beta are neg
def construct_training_data_classifier(forests, frame, loc, alpha, beta, patch_size):
	patches_pos, patches_neg = get_pos_neg_patches(frame, loc, alpha, beta, patch_size)
	codes_pos = binary_codes_test(forests, patches_pos)
	# append a column of ones to indicate pos label
	r,c = codes_pos.shape
	temp = np.ones((r,c+1))
	temp[:,:-1] = codes_pos
	train_pos = temp

	codes_neg = binary_codes_test(forests, patches_neg)
	# append a column of -1s to indicate neg label
	r,c = codes_neg.shape
	temp = np.ones((r,c+1))*(-1)
	temp[:,:-1] = codes_neg
	train_neg = temp

	train = np.concatenate((train_pos, train_neg))
	np.random.shuffle(train)

	return train

# given training data (from patches around a predicted point), train/update the classifier 
# in an online fashion
def classifier_train(train):
	all_classes = np.array([-1, 1])
	X_train = train[:,:-1]
	y_train = train[:,-1]
	# classifier.partial_fit(X_train, y_train, classes=all_classes)
	classifier1.fit(X_train, y_train)
	classifier2.fit(X_train, y_train)
	classifier3.fit(X_train, y_train)
	classifier4.fit(X_train, y_train)
	classifier5.fit(X_train, y_train)
	classifier = (classifier1, classifier2, classifier3, classifier4, classifier5)
	return classifier

# compute the confidence scores of the samples in the new frame using the classifier 
# trained on the samples of the previous frame
def compute_confidence_scores(classifier, forests, frame, loc, patch_size):
	coords, patches = get_patches(frame, loc, patch_size)
	# print(patches.shape)
	patch_codes = binary_codes_test(forests, patches)
	# find the index of the patch getting maximum score from the classifier
	classifier1, classifier2, classifier3, classifier4, classifier5 = classifier


	def normalize(scores):
		return scores/np.max(np.abs(scores))

	scores1 = normalize(classifier1.decision_function(patch_codes))
	scores2 = normalize(classifier2.decision_function(patch_codes))
	scores3 = normalize(classifier3.decision_function(patch_codes))
	scores4 = normalize(classifier4.decision_function(patch_codes))
	scores5 = normalize(classifier5.decision_function(patch_codes))

	scores = (scores1+scores2+scores3+scores4+scores5)*1.0/5.0
	# ind = np.argmax(scores)
	# return the new (predicted) location of the target
	# return coords[ind] #, (coords,scores)
	return (coords, patch_codes, scores)


# given the initial confidence score vector s0, refines the scores and returns them
def hypergraph_propagation(coords, patch_codes, scores, tau=50):
	scores = np.asarray(scores, np.float)
	# incidence matrix
	H = patch_codes.copy()
	H[H<0] = 0.0
	H = np.asarray(H, np.float)
	assert H.shape == (patch_codes.shape[0],100)
	# Dv
	v = np.sum(H, axis=1)
	v = np.asarray(v, dtype=np.float)
	v[v==0] = 1.0
	# for a in v:
		# print(a)
	# print(v==0)
	Dv = np.diag(v)
	Dvinv = np.linalg.inv(Dv)
	# De
	e = np.sum(H,axis=0)
	e = np.asarray(e, dtype=np.float)
	e[e==0] = 1.0
	De = np.diag(e)
	Deinv = np.linalg.inv(De)

	# TPM
	P = np.matmul(Dvinv,np.matmul(H, np.matmul(Deinv, H.T)))
	# alpha
	alpha = 0.99

	scores = np.squeeze(scores)
	curr_scores = scores.copy()
	for i in range(tau):
		curr_scores = alpha*np.matmul(P,curr_scores) + (1-alpha)*scores

	print(curr_scores.shape)
	ind = np.argmax(curr_scores)
	return coords[ind]


# construct a bounding box around the predicted location and save the image
def construct_bounding_box(img, img_name):
	# x, y, width, height = cv2.selectROI(img)
	x_cv = 155
	y_cv = 64
	width = 63
	height = 69
	# print(x_cv,y_cv,width,height)

	cv2.rectangle(img, (x_cv,y_cv), (x_cv+width,y_cv+height), (0,0,255), 3)
	img_name = os.path.join(output_dir,img_name)
	# print(img_name)
	cv2.imwrite(img_name, img)
	print("should be ", int(x_cv+width/2), int(y_cv+height/2))
	return int(x_cv+width/2), int(y_cv+height/2), width, height

def	save_img(img, loc, siz, img_name):
	x, y = loc
	w, h = siz
	tlX, tlY = int(x - w/2), int(y - h/2)
	cv2.rectangle(img, (tlX,tlY), (tlX+w,tlY+h), (0,0,255), 3)
	img_name = os.path.join(output_dir,img_name)
	cv2.imwrite(img_name, img)

def main():
	img_names_list = list()
	for img_name in os.listdir(frames_dir):
		if('jpg' or 'png' in img_name):
			img_names_list.append(img_name)
	img_names_list.sort()

	patch_size = 16
	alpha = 16
	beta = 48

	img0 = cv2.imread(os.path.join(frames_dir, img_names_list[0]))
	img0 = cv2.cvtColor(img0, cv2.COLOR_BGR2GRAY)
	loc_cvx, loc_cvy, w, h = construct_bounding_box(img0, img_names_list[0])
	loc_cv = (loc_cvx, loc_cvy)
	height, width= img0.shape

	# train the forests and the classifier on the first frame
	print("Frame 0:")
	print("Computing Features")
	patches_data = construct_training_data_forest(img0,loc_cv,alpha,beta,patch_size)
	np.save('patches0_NB.npy', patches_data)
	# patches_data = np.load('patches0.npy')
	print("Training Forests")
	forests = binary_codes_train(patches_data)

	# Save the forest for frame 1
	pickle_out = open("forests0_NB.pickle", "wb")
	pickle.dump(forests, pickle_out)
	pickle_out.close()

	# pickle_in = open("forests0.pickle","rb")
	# forests = pickle.load(pickle_in)

	print("Computing Codes")
	codes_data = construct_training_data_classifier(forests, img0, loc_cv, alpha, beta, patch_size)

	# Save the codes for frame 1
	pickle_out = open("codes0_NB.pickle", "wb")
	pickle.dump(codes_data, pickle_out)
	pickle_out.close()

	# pickle_in = open("codes0.pickle","rb")
	# codes_data = pickle.load(pickle_in)

	print("Initial Location:", loc_cv)
	print("Training Classifier")
	classifier = classifier_train(codes_data)

	for i, img_name in tqdm(enumerate(img_names_list)):
		if(i==0):
			continue
		print("Frame %d" %i)
		frame = cv2.imread(os.path.join(frames_dir,img_name), cv2.IMREAD_COLOR)
		frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

		print("Predicting")
		# loc_cv = compute_confidence_scores(classifier, forests, frame, loc_cv, patch_size)
		coords, patches_codes, scores = compute_confidence_scores(classifier, forests, frame, loc_cv, patch_size)
		for score in scores:
			print(score)
		loc_cv = hypergraph_propagation(coords, patches_codes, scores)
		print("New Location:", loc_cv)
		
		print("Saving Image")
		save_img(frame, loc_cv, (w,h), img_name)

		if(i%forest_update==0):
			print("Updating Perceptron Forests")
			patches_data = construct_training_data_forest(frame,loc_cv,alpha,beta,patch_size)
			forests = binary_codes_train(patches_data)

		if(i%classifier_update==0):
			print("Updating Classifier")
			codes_data = construct_training_data_classifier(forests, frame, loc_cv, alpha, beta, patch_size)
			classifier = classifier_train(codes_data)


if __name__ == "__main__":
	main()