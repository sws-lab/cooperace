// This file is part of the SV-Benchmarks collection of verification tasks:
// https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks
//
// SPDX-FileCopyrightText: 2021 F. Schuessele <schuessf@informatik.uni-freiburg.de>
// SPDX-FileCopyrightText: 2021 D. Klumpp <klumpp@informatik.uni-freiburg.de>
//
// SPDX-License-Identifier: LicenseRef-BSD-3-Clause-Attribution-Vandikas

typedef unsigned long int pthread_t;

union pthread_attr_t
{
  char __size[36];
  long int __align;
};
typedef union pthread_attr_t pthread_attr_t;

extern void __assert_fail(const char *__assertion, const char *__file,
      unsigned int __line, const char *__function)
     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__noreturn__));
void reach_error() { __assert_fail("0", "chl-chromosome-opt-symm.wvr.c", 21, __extension__ __PRETTY_FUNCTION__); }
extern int pthread_create (pthread_t *__restrict __newthread,
      const pthread_attr_t *__restrict __attr,
      void *(*__start_routine) (void *),
      void *__restrict __arg) __attribute__ ((__nothrow__)) __attribute__ ((__nonnull__ (1, 3)));
extern int pthread_join (pthread_t __th, void **__thread_return);

typedef unsigned int size_t;
extern void *malloc (size_t __size) __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__malloc__)) ;

extern int  __VERIFIER_nondet_int(void);
extern _Bool __VERIFIER_nondet_bool(void);
extern void __VERIFIER_atomic_begin(void);
extern void __VERIFIER_atomic_end(void);

extern void abort(void);
void assume_abort_if_not(int cond) {
  if(!cond) {abort();}
}

int *scoreA, *scoreB;
int res1, res2, isNullA, isNullB;

int *create_fresh_int_array(int size);

void* thread1(void* _argptr) {
  int i = 0;
  int comp;
  assume_abort_if_not(isNullA != 0);
  while (isNullB != 0 && i < 5) {
    comp = scoreA[i] < scoreB[i] ? -1 : (scoreA[i] > scoreB[i] ? 1 : 0);
    if (comp != 0) {
      res1 = comp;
      break;
    }
    i++;
  }

  return 0;
}

void* thread2(void* _argptr) {
  int i = 0;
  int comp;
  assume_abort_if_not(isNullB != 0);
  while (isNullA != 0 && i < 5) {
    comp = scoreB[i] < scoreA[i] ? -1 : (scoreB[i] > scoreA[i] ? 1 : 0);
    if (comp != 0) {
      res2 = comp;
      break;
    }
    i++;
  }

  return 0;
}

int main() {
  pthread_t t1, t2;
  
  scoreA = create_fresh_int_array(5);
  scoreB = create_fresh_int_array(5);
  isNullA = __VERIFIER_nondet_int();
  isNullB = __VERIFIER_nondet_int();
  
  // main method
  pthread_create(&t1, 0, thread1, 0);
  pthread_create(&t2, 0, thread2, 0);
  pthread_join(t1, 0);
  pthread_join(t2, 0);

  assume_abort_if_not(res1 != -res2);
  reach_error();

  return 0;
}

int *create_fresh_int_array(int size) {
  assume_abort_if_not(size >= 0);
  assume_abort_if_not(size <= (((size_t) 4294967295) / sizeof(int)));

  int* arr = (int*)malloc(sizeof(int) * (size_t)size);
  for (int i = 0; i < size; i++) {
    arr[i] = __VERIFIER_nondet_int();
  }
  return arr;
}